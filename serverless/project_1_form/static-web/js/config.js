// Only needed when this page is hosted somewhere else than the API itself
// (python -m http.server, `s3-manager host deploy`, ...).
// Paste the POST endpoint that `npm run deploy:form` printed, for example:
//   window.FORM_API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com/prod/";
// When the page is opened from the API (GET /), the Lambda fills this in by itself.
window.FORM_API_URL = "";
