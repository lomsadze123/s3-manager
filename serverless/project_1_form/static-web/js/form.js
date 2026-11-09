// The API Gateway endpoint comes from js/config.js, or from the Lambda when the page is served by GET /
const API_URL = window.FORM_API_URL;

const FIELDS = [
  "name",
  "email",
  "phone",
  "address",
  "programming_languages",
  "tools",
];

function showMessage(text, color) {
  const element = document.getElementById("form-response");
  element.textContent = text;
  element.style.color = color;
  element.style.fontWeight = "bold";
}

document.getElementById("mainForm").addEventListener("submit", (e) => {
  e.preventDefault();

  if (!API_URL) {
    showMessage(
      "Error: set FORM_API_URL in js/config.js to the API Gateway endpoint",
      "red",
    );
    return;
  }

  const data = {};
  FIELDS.forEach((field) => {
    data[field] = document.getElementById(field).value;
  });

  fetch(API_URL, {
    method: "POST",
    mode: "cors",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(data),
  })
    .then(async (response) => {
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const fields = result.fields ? ": " + result.fields.join(", ") : "";
        throw new Error(
          (result.message || "Status " + response.status) + fields,
        );
      }
      showMessage("Form submitted!", "green");
      document.getElementById("mainForm").reset();
      if (window.M) M.updateTextFields();
    })
    .catch((error) => {
      console.log("Post error: " + error);
      showMessage("Error: " + error.message, "red");
    });
});
