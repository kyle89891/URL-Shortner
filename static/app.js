const form = document.getElementById("shorten-form");
const longUrlInput = document.getElementById("long-url");
const resultBox = document.getElementById("result");
const shortUrlLink = document.getElementById("short-url-link");
const metaText = document.getElementById("meta");
const errorBox = document.getElementById("error");
const copyBtn = document.getElementById("copy-btn");
const submitBtn = form.querySelector("button[type=submit]");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  resultBox.classList.add("hidden");
  errorBox.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Shortening...";

  try {
    const response = await fetch("/api/shorten", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ long_url: longUrlInput.value }),
    });

    const data = await response.json();

    if (!response.ok) {
      const message = Array.isArray(data.detail)
        ? data.detail.map((d) => d.msg).join(", ")
        : data.detail || "Something went wrong.";
      throw new Error(message);
    }

    shortUrlLink.href = data.short_url;
    shortUrlLink.textContent = data.short_url;
    metaText.textContent = `Points to: ${data.long_url}`;
    resultBox.classList.remove("hidden");
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Shorten";
  }
});

copyBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(shortUrlLink.href);
  const original = copyBtn.textContent;
  copyBtn.textContent = "Copied!";
  setTimeout(() => (copyBtn.textContent = original), 1200);
});
