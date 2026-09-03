# SME question — director employment evidence (RCM Phase 1)

**Related:** `docs/adr/0002-rcm-director-evidence-phase1-grain.md`
**Status:** Phase 1 proceeding on the assumption below (Steve, 2026-09-03).
This question is for confirmation, not a blocker.

## Question

For the RCM director-employment test (renting of motor vehicle / director's
sitting fees type provisions), we currently have one source of director
data: a client-supplied director list (name, DIN, designation, appointment
date, cessation date) — this is "what the client's own records say."

We do NOT yet have Form DIR-12 (the ROC filing that independently proves an
appointment/cessation with a government filing reference), because building
it requires a broader capability ("Forensic Mode") that hasn't been built
for anything yet, not just this.

**Is it acceptable for Phase 1's RCM director-employment check to rely only
on the client's own director list** — i.e., "does our record show this
person was a director around this invoice date" — **or does the rule need
independent ROC-filing-backed proof (Form DIR-12) to be defensible before we
can flag/act on it?**

If the client's list is enough for Phase 1, no further action needed — that
is what has been built. If DIR-12-backed proof is required, that is a
larger, separately scoped piece of work (Forensic Mode) and should be
scheduled rather than assumed away.
