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
      if (form.hasAttribute("data-auto")) autoAdvance(form);
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

// Auto-advance (opt-in on the Settings page). Ask Wikipedia's API for the
// article's latest revision every few seconds. When it changes, ask the tool
// once whether that revision fixed the infobox; if so, count down and load
// the next article.
const POLL_MS = 5000;
const GIVE_UP_MS = 15 * 60 * 1000;
const COUNTDOWN_S = 3;

function autoAdvance(form) {
  const box = document.querySelector("#after-handoff .auto-status");
  const next = document.querySelector("#after-handoff .next-btn");
  if (!box || !next || form.dataset.polling) return;
  form.dataset.polling = "1";

  const status = box.querySelector("[role=status]");
  const stay = box.querySelector("[data-stay]");
  const say = (text) => { box.hidden = false; status.textContent = text; };
  const started = Date.now();
  let timer;

  const api = new URL(form.dataset.api);
  api.search = new URLSearchParams({
    action: "query", prop: "revisions", titles: form.dataset.title, rvprop: "ids",
    format: "json", formatversion: "2", origin: "*",
  });

  async function latestRevid() {
    const data = await (await fetch(api, { cache: "no-store" })).json();
    return data?.query?.pages?.[0]?.revisions?.[0]?.revid;
  }

  async function fixedBy(revid) {
    const check = new URL(form.dataset.check, location.href);
    check.search = new URLSearchParams({ title: form.dataset.title, revid, before: form.dataset.before });
    const r = await fetch(check, { cache: "no-store" });
    if (!r.ok) throw new Error("check failed");
    return (await r.json()).done;
  }

  async function poll() {
    if (Date.now() - started > GIVE_UP_MS) {
      say("Stopped checking for your edit. Click Next article when you're ready.");
      return;
    }
    try {
      const revid = await latestRevid();
      if (revid && String(revid) !== form.dataset.revid) {
        if (await fixedBy(revid)) return countdown();
        say("The article was edited, but |alma_mater= is still there. Check it on Wikipedia, or move on.");
        return;
      }
    } catch (e) {
      // A network hiccup or a busy server: just try again next time.
    }
    timer = setTimeout(poll, POLL_MS);
  }

  function countdown() {
    let left = COUNTDOWN_S;
    stay.hidden = false;
    stay.addEventListener("click", () => {
      clearTimeout(timer);
      stay.hidden = true;
      say("Published ✓");
    }, { once: true });
    const tick = () => {
      if (left === 0) {
        location.href = next.href;
        return;
      }
      say(`Published ✓ Next article in ${left}…`);
      left -= 1;
      timer = setTimeout(tick, 1000);
    };
    tick();
  }

  say("Waiting for you to publish on Wikipedia…");
  timer = setTimeout(poll, POLL_MS);
}
