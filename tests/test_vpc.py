"""Tests for vpc_manager.py with a fake EC2 client (no AWS calls)."""

from unittest.mock import MagicMock, patch

import pytest

import vpc_manager
from tests.conftest import make_client_error


@pytest.fixture
def ec2():
    """A fake EC2 client that answers every create_* call with a predictable ID."""
    fake = MagicMock()
    fake.describe_vpcs.return_value = {"Vpcs": []}
    fake.create_vpc.return_value = {"Vpc": {"VpcId": "vpc-1"}}
    fake.create_internet_gateway.return_value = {"InternetGateway": {"InternetGatewayId": "igw-1"}}
    fake.describe_availability_zones.return_value = {
        "AvailabilityZones": [{"ZoneName": "us-east-1a"}, {"ZoneName": "us-east-1b"}]
    }
    fake.create_subnet.side_effect = [
        {"Subnet": {"SubnetId": "subnet-public"}},
        {"Subnet": {"SubnetId": "subnet-private"}},
    ]
    fake.create_route_table.side_effect = [
        {"RouteTable": {"RouteTableId": "rtb-public"}},
        {"RouteTable": {"RouteTableId": "rtb-private"}},
    ]
    return fake


@pytest.fixture
def existing_vpc(ec2):
    """The fake client now describes a VPC created earlier, with IGW, subnets and route tables."""
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1"}]}
    ec2.describe_internet_gateways.return_value = {"InternetGateways": [{"InternetGatewayId": "igw-1"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [{"SubnetId": "subnet-public"}, {"SubnetId": "subnet-private"}]
    }
    ec2.describe_route_tables.return_value = {
        "RouteTables": [
            {"RouteTableId": "rtb-main", "Associations": [{"Main": True, "RouteTableAssociationId": "a-0"}]},
            {"RouteTableId": "rtb-public", "Associations": []},
            {"RouteTableId": "rtb-private", "Associations": []},
        ]
    }
    return ec2


def tag_value(call, key):
    tags = call.kwargs["TagSpecifications"][0]["Tags"]
    return next(tag["Value"] for tag in tags if tag["Key"] == key)


# --------------------------------------------------------------------------- CIDR validation


def test_validate_cidrs_accepts_defaults():
    vpc, public, private = vpc_manager.validate_cidrs("10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24")
    assert str(vpc) == "10.0.0.0/16"
    assert str(public) == "10.0.1.0/24"
    assert str(private) == "10.0.2.0/24"


@pytest.mark.parametrize(
    "vpc_cidr, public_cidr, private_cidr, message",
    [
        ("not-a-cidr", "10.0.1.0/24", "10.0.2.0/24", "not valid"),
        ("10.0.0.1/16", "10.0.1.0/24", "10.0.2.0/24", "host bits"),
        ("10.0.0.0/33", "10.0.1.0/24", "10.0.2.0/24", "not valid"),
        ("fd00::/56", "10.0.1.0/24", "10.0.2.0/24", "IPv4"),
        ("10.0.0.0/8", "10.0.1.0/24", "10.0.2.0/24", "prefix"),
        ("10.0.0.0/16", "10.0.1.0/29", "10.0.2.0/24", "prefix"),
        ("10.0.0.0/16", "10.1.1.0/24", "10.0.2.0/24", "not inside"),
        ("10.0.0.0/16", "10.0.1.0/24", "10.0.1.0/25", "overlap"),
    ],
)
def test_validate_cidrs_rejects_bad_input(vpc_cidr, public_cidr, private_cidr, message):
    with pytest.raises(ValueError, match=message):
        vpc_manager.validate_cidrs(vpc_cidr, public_cidr, private_cidr)


# --------------------------------------------------------------------------- create


def test_create_builds_everything(ec2):
    resources = vpc_manager.create_infrastructure(
        ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24"
    )

    assert resources == {
        "vpc": "vpc-1",
        "internet_gateway": "igw-1",
        "public_subnet": "subnet-public",
        "private_subnet": "subnet-private",
        "public_route_table": "rtb-public",
        "private_route_table": "rtb-private",
    }
    ec2.create_vpc.assert_called_once()
    assert ec2.create_vpc.call_args.kwargs["CidrBlock"] == "10.0.0.0/16"
    ec2.attach_internet_gateway.assert_called_once_with(InternetGatewayId="igw-1", VpcId="vpc-1")
    ec2.modify_subnet_attribute.assert_called_once_with(
        SubnetId="subnet-public", MapPublicIpOnLaunch={"Value": True}
    )


def test_create_tags_every_resource(ec2):
    vpc_manager.create_infrastructure(ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24")

    assert tag_value(ec2.create_vpc.call_args, "Name") == "my-vpc"
    assert tag_value(ec2.create_internet_gateway.call_args, "Name") == "my-vpc-igw"
    subnet_names = [tag_value(c, "Name") for c in ec2.create_subnet.call_args_list]
    assert subnet_names == ["my-vpc-public-subnet", "my-vpc-private-subnet"]
    rt_names = [tag_value(c, "Name") for c in ec2.create_route_table.call_args_list]
    assert rt_names == ["my-vpc-public-rt", "my-vpc-private-rt"]
    assert tag_value(ec2.create_vpc.call_args, "ManagedBy") == "vpc_manager"


def test_only_public_route_table_routes_to_internet(ec2):
    vpc_manager.create_infrastructure(ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24")

    ec2.create_route.assert_called_once_with(
        RouteTableId="rtb-public", DestinationCidrBlock="0.0.0.0/0", GatewayId="igw-1"
    )
    associations = [c.kwargs for c in ec2.associate_route_table.call_args_list]
    assert associations == [
        {"RouteTableId": "rtb-public", "SubnetId": "subnet-public"},
        {"RouteTableId": "rtb-private", "SubnetId": "subnet-private"},
    ]


def test_create_uses_given_availability_zone(ec2):
    vpc_manager.create_infrastructure(
        ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24", az="us-east-1c"
    )

    ec2.describe_availability_zones.assert_not_called()
    zones = {c.kwargs["AvailabilityZone"] for c in ec2.create_subnet.call_args_list}
    assert zones == {"us-east-1c"}


def test_create_picks_first_available_zone(ec2):
    vpc_manager.create_infrastructure(ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24")

    zones = {c.kwargs["AvailabilityZone"] for c in ec2.create_subnet.call_args_list}
    assert zones == {"us-east-1a"}


def test_create_refuses_existing_name(ec2):
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-old"}]}

    with pytest.raises(RuntimeError, match="already exists"):
        vpc_manager.create_infrastructure(ec2, "my-vpc", "10.0.0.0/16", "10.0.1.0/24", "10.0.2.0/24")
    ec2.create_vpc.assert_not_called()


# --------------------------------------------------------------------------- destroy


def test_destroy_deletes_in_safe_order(existing_vpc):
    deleted = vpc_manager.destroy_infrastructure(existing_vpc, "my-vpc")

    order = [name for name, _, _ in existing_vpc.mock_calls if name.startswith(("detach", "delete"))]
    assert order == [
        "detach_internet_gateway",
        "delete_internet_gateway",
        "delete_subnet",
        "delete_subnet",
        "delete_route_table",
        "delete_route_table",
        "delete_vpc",
    ]
    assert deleted == ["igw-1", "subnet-public", "subnet-private", "rtb-public", "rtb-private", "vpc-1"]


def test_destroy_keeps_main_route_table(existing_vpc):
    vpc_manager.destroy_infrastructure(existing_vpc, "my-vpc")

    deleted_tables = [c.kwargs["RouteTableId"] for c in existing_vpc.delete_route_table.call_args_list]
    assert "rtb-main" not in deleted_tables


def test_destroy_disassociates_leftover_associations(existing_vpc):
    existing_vpc.describe_route_tables.return_value = {
        "RouteTables": [
            {"RouteTableId": "rtb-public", "Associations": [{"RouteTableAssociationId": "assoc-1"}]}
        ]
    }

    vpc_manager.destroy_infrastructure(existing_vpc, "my-vpc")

    existing_vpc.disassociate_route_table.assert_called_once_with(AssociationId="assoc-1")


def test_destroy_handles_partial_setup(existing_vpc):
    existing_vpc.describe_internet_gateways.return_value = {"InternetGateways": []}
    existing_vpc.describe_subnets.return_value = {"Subnets": []}
    existing_vpc.describe_route_tables.return_value = {"RouteTables": []}

    assert vpc_manager.destroy_infrastructure(existing_vpc, "my-vpc") == ["vpc-1"]
    existing_vpc.delete_vpc.assert_called_once_with(VpcId="vpc-1")


def test_destroy_only_looks_for_vpcs_created_by_script(existing_vpc):
    vpc_manager.destroy_infrastructure(existing_vpc, "my-vpc")

    filters = existing_vpc.describe_vpcs.call_args.kwargs["Filters"]
    assert {"Name": "tag:Name", "Values": ["my-vpc"]} in filters
    assert {"Name": "tag:ManagedBy", "Values": ["vpc_manager"]} in filters


def test_destroy_missing_vpc_raises(ec2):
    with pytest.raises(LookupError):
        vpc_manager.destroy_infrastructure(ec2, "missing")
    ec2.delete_vpc.assert_not_called()


# --------------------------------------------------------------------------- CLI


def run_cli(argv, fake_client):
    with patch.object(vpc_manager, "init_client", return_value=fake_client):
        return vpc_manager.main(argv)


def test_cli_requires_action():
    with pytest.raises(SystemExit) as error:
        vpc_manager.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("action", ["create", "destroy"])
def test_cli_requires_vpc_name(action):
    with pytest.raises(SystemExit) as error:
        vpc_manager.main([action])
    assert error.value.code == 2


def test_cli_rejects_bad_cidr_before_calling_aws(ec2):
    with pytest.raises(SystemExit) as error:
        run_cli(["create", "--vpc-name", "x", "--vpc-cidr", "10.0.0.1/16"], ec2)
    assert error.value.code == 2
    ec2.create_vpc.assert_not_called()


def test_cli_create_prints_ids(ec2, capsys):
    code = run_cli(["create", "--vpc-name", "my-vpc", "--vpc-cidr", "10.0.0.0/16"], ec2)

    assert code == 0
    output = capsys.readouterr().out
    assert "vpc-1" in output
    assert "igw-1" in output


def test_cli_create_aws_error_suggests_destroy(ec2, capsys):
    ec2.create_internet_gateway.side_effect = make_client_error("InternetGatewayLimitExceeded")

    assert run_cli(["create", "--vpc-name", "my-vpc"], ec2) == 1
    assert "destroy --vpc-name my-vpc" in capsys.readouterr().err


def test_cli_destroy_success(existing_vpc, capsys):
    assert run_cli(["destroy", "--vpc-name", "my-vpc"], existing_vpc) == 0
    assert "destroyed" in capsys.readouterr().out


def test_cli_destroy_not_found(ec2, capsys):
    assert run_cli(["destroy", "--vpc-name", "missing"], ec2) == 1
    assert "No VPC named 'missing'" in capsys.readouterr().err


def test_cli_destroy_dependency_violation(existing_vpc, capsys):
    existing_vpc.delete_vpc.side_effect = make_client_error("DependencyViolation")

    assert run_cli(["destroy", "--vpc-name", "my-vpc"], existing_vpc) == 1
    assert "DependencyViolation" in capsys.readouterr().err
