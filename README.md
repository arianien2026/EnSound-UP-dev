# EnSound UP — Step 1 Sentence Interaction Prototype

Payment and entitlement planning: [Payment Architecture](docs/PAYMENT_ARCHITECTURE.md).

This prototype implements the first EnSound UP flow:

1. Type an English sentence.
2. The sentence is split into selectable word chips.
3. Only one word can be selected at a time.
4. Play the selected word at normal speed.
5. Play it slowly.
6. Repeat it in a loop.

## Run

```bash
npm install
npm run dev
```

Then open the Vite URL shown in the terminal.

## Current technical choice

Audio uses the browser Web Speech API (`SpeechSynthesisUtterance`) so Step 1 can be tested without a backend or paid API.

## Intentionally not included yet

- IPA
- syllable breakdown
- stress marking
- pronunciation scoring
- user accounts
- review history
- payments

Those belong to later EnSound UP steps.
