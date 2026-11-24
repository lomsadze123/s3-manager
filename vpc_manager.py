"""Create or destroy a small VPC with argparse and boto3.

    python vpc_manager.py create  --vpc-name my-vpc --vpc-cidr 10.0.0.0/16
    python vpc_manager.py destroy --vpc-name my-vpc

create builds: a VPC (Name tag), an Internet Gateway attached to it, one public and one private
subnet, and one route table per subnet. Only the public route table gets a 0.0.0.0/0 route to
the Internet Gateway, so only the public subnet can reach the internet.

destroy finds everything by the VPC's Name tag and deletes it in the order AWS requires:
detach the Internet Gateway, delete it, delete the subnets and route tables, delete the VPC.
Any other order fails with DependencyViolation.

Every resource gets the tag ManagedBy=vpc_manager, so destroy never touches a VPC this script
did not create, even if it has the same name.
"""

import argparse
import ipaddress
import logging
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

# Reuse the s3-manager client helper (.env credentials, AWS_SESSION_TOKEN for AWS Academy).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from s3_manager.client import init_client  # noqa: E402

logger = logging.getLogger("vpc_manager")

MANAGED_BY_KEY = "ManagedBy"
MANAGED_BY_VALUE = "vpc_manager"

DEFAULT_VPC_CIDR = "10.0.0.0/16"
DEFAULT_PUBLIC_CIDR = "10.0.1.0/24"
DEFAULT_PRIVATE_CIDR = "10.0.2.0/24"

# AWS allows VPC and subnet CIDR blocks between /16 and /28.
MIN_PREFIX = 16
MAX_PREFIX = 28


# --------------------------------------------------------------------------- CIDR validation


def parse_cidr(cidr, label):
    """Turn a CIDR string into an IPv4Network, or raise ValueError with a readable message.

    strict=True rejects blocks with host bits set, e.g. 10.0.0.1/16 (should be 10.0.0.0/16).
    """
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError as error:
        raise ValueError(f"{label} CIDR '{cidr}' is not valid: {error}") from None

    if network.version != 4:
        raise ValueError(f"{label} CIDR '{cidr}' must be IPv4")
    if not MIN_PREFIX <= network.prefixlen <= MAX_PREFIX:
        raise ValueError(
            f"{label} CIDR '{cidr}' must have a prefix between /{MIN_PREFIX} and /{MAX_PREFIX} "
            f"(AWS limit), got /{network.prefixlen}"
        )
    return network


def validate_cidrs(vpc_cidr, public_cidr, private_cidr):
    """Check the three CIDR blocks before anything is created in AWS.

    Rules: valid IPv4 networks, prefix /16-/28, both subnets inside the VPC,
    and the two subnets must not overlap. Raises ValueError on the first problem.
    """
    vpc = parse_cidr(vpc_cidr, "VPC")
    public = parse_cidr(public_cidr, "Public subnet")
    private = parse_cidr(private_cidr, "Private subnet")

    if not vpc.is_private:
        logger.warning("VPC CIDR %s is not a private (RFC 1918) range", vpc)

    for label, subnet in (("Public subnet", public), ("Private subnet", private)):
        if not subnet.subnet_of(vpc):
            raise ValueError(f"{label} CIDR {subnet} is not inside the VPC CIDR {vpc}")

    if public.overlaps(private):
        raise ValueError(f"Public subnet {public} and private subnet {private} overlap")

    return vpc, public, private


# --------------------------------------------------------------------------- helpers


def tag_spec(resource_type, name):
    """TagSpecifications for a create_* call: a Name tag plus the ManagedBy tag."""
    return [
        {
            "ResourceType": resource_type,
            "Tags": [
                {"Key": "Name", "Value": name},
                {"Key": MANAGED_BY_KEY, "Value": MANAGED_BY_VALUE},
            ],
        }
    ]


def find_vpcs(ec2, name):
    """Return the IDs of VPCs this script created with the given Name tag."""
    response = ec2.describe_vpcs(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": f"tag:{MANAGED_BY_KEY}", "Values": [MANAGED_BY_VALUE]},
        ]
    )
    return [vpc["VpcId"] for vpc in response.get("Vpcs", [])]


def pick_availability_zone(ec2):
    """Return the first available Availability Zone of the client's region."""
    response = ec2.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )
    zones = response.get("AvailabilityZones", [])
    if not zones:
        raise RuntimeError("No available Availability Zone found in this region")
    return zones[0]["ZoneName"]


# --------------------------------------------------------------------------- create


def create_vpc(ec2, name, cidr):
    """Create the VPC, wait until it is available and turn on DNS hostnames."""
    vpc_id = ec2.create_vpc(CidrBlock=cidr, TagSpecifications=tag_spec("vpc", name))["Vpc"]["VpcId"]
    logger.info("Created VPC %s (%s)", vpc_id, cidr)

    ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    return vpc_id


def create_internet_gateway(ec2, name, vpc_id):
    """Create an Internet Gateway and attach it to the VPC."""
    igw_id = ec2.create_internet_gateway(
        TagSpecifications=tag_spec("internet-gateway", f"{name}-igw")
    )["InternetGateway"]["InternetGatewayId"]
    logger.info("Created Internet Gateway %s", igw_id)

    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    logger.info("Attached Internet Gateway %s to VPC %s", igw_id, vpc_id)
    return igw_id


def create_subnet(ec2, name, vpc_id, cidr, az, public):
    """Create a subnet. A public subnet also gives new instances a public IP automatically."""
    kind = "public" if public else "private"
    subnet_id = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock=cidr,
        AvailabilityZone=az,
        TagSpecifications=tag_spec("subnet", f"{name}-{kind}-subnet"),
    )["Subnet"]["SubnetId"]
    logger.info("Created %s subnet %s (%s, %s)", kind, subnet_id, cidr, az)

    if public:
        ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": True})
    return subnet_id


def create_route_table(ec2, name, vpc_id, subnet_id, igw_id=None):
    """Create a route table for one subnet and associate it.

    With igw_id (public subnet) a default route 0.0.0.0/0 -> Internet Gateway is added.
    Without it (private subnet) the table only has the automatic local route.
    """
    kind = "public" if igw_id else "private"
    rt_id = ec2.create_route_table(
        VpcId=vpc_id, TagSpecifications=tag_spec("route-table", f"{name}-{kind}-rt")
    )["RouteTable"]["RouteTableId"]
    logger.info("Created %s route table %s", kind, rt_id)

    if igw_id:
        ec2.create_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
        logger.info("Added route 0.0.0.0/0 -> %s to %s", igw_id, rt_id)

    ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)
    return rt_id


def create_infrastructure(ec2, name, vpc_cidr, public_cidr, private_cidr, az=None):
    """Create the whole setup and return a dict with every resource ID."""
    if find_vpcs(ec2, name):
        raise RuntimeError(f"A VPC named '{name}' already exists. Destroy it first or pick another name.")

    resources = {"vpc": create_vpc(ec2, name, vpc_cidr)}
    resources["internet_gateway"] = create_internet_gateway(ec2, name, resources["vpc"])

    az = az or pick_availability_zone(ec2)
    resources["public_subnet"] = create_subnet(ec2, name, resources["vpc"], public_cidr, az, public=True)
    resources["private_subnet"] = create_subnet(ec2, name, resources["vpc"], private_cidr, az, public=False)

    resources["public_route_table"] = create_route_table(
        ec2, name, resources["vpc"], resources["public_subnet"], igw_id=resources["internet_gateway"]
    )
    resources["private_route_table"] = create_route_table(
        ec2, name, resources["vpc"], resources["private_subnet"]
    )
    return resources


# --------------------------------------------------------------------------- destroy


def vpc_filter(vpc_id):
    return [{"Name": "vpc-id", "Values": [vpc_id]}]


def delete_internet_gateways(ec2, vpc_id):
    """Step 1 + 2: detach every Internet Gateway from the VPC, then delete it."""
    deleted = []
    response = ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
    )
    for igw in response.get("InternetGateways", []):
        igw_id = igw["InternetGatewayId"]
        ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        logger.info("Detached Internet Gateway %s from %s", igw_id, vpc_id)
        ec2.delete_internet_gateway(InternetGatewayId=igw_id)
        logger.info("Deleted Internet Gateway %s", igw_id)
        deleted.append(igw_id)
    return deleted


def delete_subnets(ec2, vpc_id):
    """Step 3a: delete every subnet (this also removes its route table association)."""
    deleted = []
    for subnet in ec2.describe_subnets(Filters=vpc_filter(vpc_id)).get("Subnets", []):
        ec2.delete_subnet(SubnetId=subnet["SubnetId"])
        logger.info("Deleted subnet %s", subnet["SubnetId"])
        deleted.append(subnet["SubnetId"])
    return deleted


def delete_route_tables(ec2, vpc_id):
    """Step 3b: delete every custom route table. The main one is deleted together with the VPC."""
    deleted = []
    for table in ec2.describe_route_tables(Filters=vpc_filter(vpc_id)).get("RouteTables", []):
        associations = table.get("Associations", [])
        if any(assoc.get("Main") for assoc in associations):
            continue
        for assoc in associations:
            ec2.disassociate_route_table(AssociationId=assoc["RouteTableAssociationId"])
        ec2.delete_route_table(RouteTableId=table["RouteTableId"])
        logger.info("Deleted route table %s", table["RouteTableId"])
        deleted.append(table["RouteTableId"])
    return deleted


def destroy_infrastructure(ec2, name):
    """Delete every VPC with this name (created by this script) and everything inside it.

    Returns the list of deleted resource IDs. Raises LookupError if nothing was found.
    """
    vpc_ids = find_vpcs(ec2, name)
    if not vpc_ids:
        raise LookupError(f"No VPC named '{name}' created by vpc_manager was found")

    deleted = []
    for vpc_id in vpc_ids:
        deleted += delete_internet_gateways(ec2, vpc_id)
        deleted += delete_subnets(ec2, vpc_id)
        deleted += delete_route_tables(ec2, vpc_id)
        ec2.delete_vpc(VpcId=vpc_id)
        logger.info("Deleted VPC %s", vpc_id)
        deleted.append(vpc_id)
    return deleted


# --------------------------------------------------------------------------- CLI


def build_parser():
    parser = argparse.ArgumentParser(
        prog="vpc_manager.py",
        description="Create or destroy a VPC with a public and a private subnet.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    actions = parser.add_subparsers(dest="action", required=True, metavar="{create,destroy}")

    create = actions.add_parser("create", help="create the VPC, subnets, route tables and IGW")
    create.add_argument("--vpc-name", required=True, help="value of the VPC's Name tag")
    create.add_argument("--vpc-cidr", default=DEFAULT_VPC_CIDR, help=f"default: {DEFAULT_VPC_CIDR}")
    create.add_argument(
        "--public-cidr", default=DEFAULT_PUBLIC_CIDR, help=f"default: {DEFAULT_PUBLIC_CIDR}"
    )
    create.add_argument(
        "--private-cidr", default=DEFAULT_PRIVATE_CIDR, help=f"default: {DEFAULT_PRIVATE_CIDR}"
    )
    create.add_argument("--az", help="Availability Zone, e.g. us-east-1a (default: first available)")
    create.add_argument("--region", help="AWS region (default: AWS_REGION or us-east-1)")

    destroy = actions.add_parser("destroy", help="delete everything created by 'create'")
    destroy.add_argument("--vpc-name", required=True, help="Name tag of the VPC to delete")
    destroy.add_argument("--region", help="AWS region (default: AWS_REGION or us-east-1)")
    return parser


def run_create(ec2, args):
    try:
        resources = create_infrastructure(
            ec2, args.vpc_name, args.vpc_cidr, args.public_cidr, args.private_cidr, args.az
        )
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except ClientError as error:
        print(f"AWS error while creating: {error}", file=sys.stderr)
        print(
            f"Some resources may already exist. Clean up with:\n"
            f"  python vpc_manager.py destroy --vpc-name {args.vpc_name}",
            file=sys.stderr,
        )
        return 1

    print(f"VPC '{args.vpc_name}' created:")
    for key, value in resources.items():
        print(f"  {key:<20} {value}")
    return 0


def run_destroy(ec2, args):
    try:
        deleted = destroy_infrastructure(ec2, args.vpc_name)
    except LookupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "DependencyViolation":
            print(
                "Error: DependencyViolation - something else still uses this VPC "
                "(e.g. an EC2 instance, a NAT gateway or a network interface). "
                "Delete it and run destroy again.",
                file=sys.stderr,
            )
        else:
            print(f"AWS error while destroying: {error}", file=sys.stderr)
        return 1

    print(f"VPC '{args.vpc_name}' destroyed. Deleted {len(deleted)} resources:")
    for resource_id in deleted:
        print(f"  {resource_id}")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.action == "create":
        try:
            validate_cidrs(args.vpc_cidr, args.public_cidr, args.private_cidr)
        except ValueError as error:
            parser.error(str(error))

    try:
        ec2 = init_client(args.region, service="ec2")
        if args.action == "create":
            return run_create(ec2, args)
        return run_destroy(ec2, args)
    except (NoCredentialsError, BotoCoreError) as error:
        print(f"AWS connection error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
