# Brand & Website Guardrails Skill (Nova Studio)

When modifying the client's website, strictly follow these rules:
1. **Forbidden Colors:** Never use bright red (`#ff0000`, `#ef4444`) or neon yellow for backgrounds or buttons. Preferred palette: deep forest green (`#14532d`), navy blue (`#1e3a8a`), or warm charcoal (`#1f2937`).
2. **Tone of Voice:** All Bulgarian texts on the website must address visitors formally using **"Вие" / "Вашия"** (never informal "ти").
3. **Mandatory Contact Info:** Never remove or hide the phone number or email address from the `contact` section.
4. **Approval Workflow:** Whenever the user says they are happy with the preview, automatically call `create_approval_ticket` so the agency's developers receive the JSON diff.
