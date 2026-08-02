# Frozen mission: first vertical slice

This file is the scope authority for implementation and final acceptance.

## Mission

Prove the reverse-CRM value loop without Hermes, Telegram, or cloud services:

```text
submit document
-> deterministic extraction and bounded candidate retrieval
-> proposed relationship to an existing property/account/asset
-> evidence-code validation
-> human confirmation
-> successful relationship-based query
```

## Required anchor fixtures

The fixture seed creates all candidate subjects before document ingestion. Ingestion must never create a property, account, or asset automatically.

### Rental council rates

- Existing subjects: a rental property, an occupied home, and the relevant council account.
- Document: synthetic council rates notice for the rental property.
- Deterministic signals: issuer, service/property address, and council account or assessment suffix.
- Expected proposal: the notice `concerns_property` the rental property; the correct property is within a maximum of three persisted candidates.
- Required evidence codes: `issuer_exact`, `address_exact`, and `account_suffix_exact` where present in the fixture.
- Query proof: querying documents related to the rental property returns the confirmed rates notice with its evidence explanation.

### Occupied-home mortgage statement

- Existing subjects: the occupied home, the rental property, and the mortgage account.
- Document: synthetic mortgage statement for the occupied home.
- Deterministic signals: lender, mortgaged-property address, and mortgage account suffix.
- Expected proposal: the statement `concerns_property` the occupied home; it does not assert ownership or borrower status.
- Required evidence codes: `issuer_exact`, `address_exact`, and `account_suffix_exact` where present in the fixture.
- Query proof: querying documents related to the occupied home returns the confirmed mortgage statement with its evidence explanation.

### Fridge receipt

- Existing subjects: a fridge asset, at least one plausible appliance distractor, and the relevant merchant/account record if modelled.
- Document: synthetic receipt for the fridge.
- Deterministic signals: merchant, purchase date/amount, make/model, and serial or order reference where present.
- Expected proposal: the receipt is `purchase_evidence_for` the existing fridge asset.
- Required evidence codes: `issuer_exact` or `merchant_alias_exact`, plus `model_exact` and `serial_exact` or `order_reference_exact` where present in the fixture.
- Query proof: querying documents related to the fridge returns the confirmed receipt with its evidence explanation.

## Acceptance invariants

1. Candidate generation is deterministic and considers only existing records.
2. At most three candidate relationships are persisted for one document.
3. Every proposal contains evidence codes drawn from a closed registry.
4. Every evidence code is validated against a stored extraction signal for that document and candidate; callers cannot merely claim a code.
5. Confirmation is an explicit human action and transactionally records the relationship, provenance, decision, and audit event.
6. Relationship-based retrieval joins through the confirmed relationship; it is not an OCR keyword-search shortcut.
7. Re-ingesting the same fixture is idempotent and does not duplicate evidence, proposals, relationships, or decisions.
8. The occupied-home mortgage case cannot create or confirm ownership or borrower claims.
9. The core acceptance suite runs with network disabled and without Hermes or Telegram configuration.

## Explicit exclusions

- Full email synchronisation or mailbox backfill.
- Automatic creation of subjects from extracted documents.
- Obligations, reminders, payment workflows, or due-date automation.
- Graph databases, embeddings, vector search, or open-ended knowledge graphs.
- A SPA, design system, dashboard suite, or large frontend.
- Hermes and Telegram implementation in this slice.
- Model-based extraction or classification in the acceptance path.

## Rubber-duck authority

The independent adversarial rubber duck may reject the plan or implementation for failing this frozen mission, its acceptance invariants, the three fixtures, maintainability, security, or test credibility. It may not require new product features, integrations, infrastructure, or broader domain modelling during final review.

