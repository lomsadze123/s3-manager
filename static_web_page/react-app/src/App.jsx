import { useState } from "react";

const STEPS = [
  "npm run build creates the dist/ folder",
  "s3-manager host deploy uploads dist/ with the right Content-Type",
  "S3 static website hosting serves index.html",
];

export default function App() {
  const [count, setCount] = useState(0);

  return (
    <main className="card">
      <h1>Beka Lomsadze</h1>
      <p className="subtitle">A React app hosted on Amazon S3</p>

      <button onClick={() => setCount((value) => value + 1)}>Clicked {count} times</button>
      <p className="hint">The counter works, so the JavaScript bundle loaded from S3.</p>

      <ol>
        {STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </main>
  );
}
