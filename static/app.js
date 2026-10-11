// Progressive enhancement only. Everything works without JavaScript.
document.documentElement.classList.add("js");

document.addEventListener("DOMContentLoaded", () => {
  // After handing off to Wikipedia, point the user at the next article.
  for (const form of document.querySelectorAll("form[data-handoff]")) {
    form.addEventListener("submit", () => {
      document.documentElement.classList.add("handed-off");
      const steps = document.querySelectorAll(".steps li");
      steps.forEach((li, i) => {
        li.classList.toggle("done", i < steps.length - 1);
        li.classList.toggle("current", i === steps.length - 1);
      });
      const next = document.querySelector("#after-handoff .next-btn");
      if (next) setTimeout(() => next.focus(), 100);
    });
  }

  // "Reset to suggestion" appears once the suggested value has been edited.
  const textarea = document.querySelector("textarea[data-original]");
  const reset = document.querySelector("[data-reset]");
  if (textarea && reset) {
    const sync = () => { reset.hidden = textarea.value === textarea.dataset.original; };
    textarea.addEventListener("input", sync);
    reset.addEventListener("click", () => {
      textarea.value = textarea.dataset.original;
      sync();
      textarea.focus();
    });
    sync();
  }

  // "Go back and change it" keeps the user's edits by going back in history.
  const back = document.querySelector("[data-back]");
  if (back && history.length > 1) {
    back.addEventListener("click", (e) => {
      e.preventDefault();
      history.back();
    });
  }
});
