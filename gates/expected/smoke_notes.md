# What the smoke runs showed before the properties gates ran

Smoke runs are not gates. They were single runs to find breakage in the new flow before the scripted gates.

- cbay, property p1: discovery found the one real way into the cart and planned 6 cases; all held. Problems seen
  and fixed afterwards: cases referred to each other's leftover data although each starts from a reset; a sold-out
  case was planned for a property whose wording says "in-stock"; one case assumed a starting value it had not read.
- lip-sync, agent version, properties g1 to g3: g1 failed as it should (GPU driver not loaded, then loaded after
  the first render). g3 held. g2 came back "needs your decision", which I had not expected: the app cuts text off
  at 500 characters, discovery found that in the code, planned a 600-character case marked as not settled by the
  wording, and that case broke. The property as I first wrote it had no length limit. I had forgotten the cap; the
  expectation was wrong, not the verifier. The sentence now says "of up to 500 characters".
- Also seen in that run: discovery planned a case that needs the server restarted with a different environment
  variable, which nobody can do; it came back "could not run". Discovery is now told to plan only for the running
  app as it is.
