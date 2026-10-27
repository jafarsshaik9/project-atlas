"use strict";
document.getElementById("login-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("password"), button = event.target.querySelector("button"), error = document.getElementById("login-error");
  error.textContent = ""; button.disabled = true;
  try {
    const response = await fetch("/auth/login", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({password:input.value}), credentials:"same-origin"});
    if (!response.ok) throw new Error("Login unsuccessful or temporarily rate limited.");
    location.replace("/");
  } catch (failure) { error.textContent = "Login unsuccessful. Check your password, or wait five minutes after repeated failures."; }
  finally { input.value = ""; button.disabled = false; }
});

