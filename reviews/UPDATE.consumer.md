# What Round 2 should improve for readers

Round 1 made both projects much easier to enter. Keep that structure.

The next improvement is trust: when a page says something is implemented, safe, or confirmed, a reader should be able to rely on it.

For **Raves of Qud**, that means the protocol page must describe the data and commands the current mod really sends, and the roadmap must acknowledge that persistent world storage already exists. The quickstart also needs one missing directory-creation command so a new install does not fail immediately.

For **highvisor**, that means examples must use real RPC fields, remote-looking opcodes must never fall through to a local agent, and “success” must distinguish “the OS accepted an input event” from “the app visibly reacted.” A shared bridge token also grants on-demand screenshot requests without another local prompt; the docs should say that plainly.

The desired reader experience remains:

- one minute to understand the product;
- five minutes to reach a verified result;
- precise engineering contracts when extending it;
- no surprise gap between “planned,” “posted,” and “confirmed.”

The large README and `CLAUDE.md` cuts should not be reversed. Restore only small operational guardrails; keep the historical detail linked outside the priming path.
