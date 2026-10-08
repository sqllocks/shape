# Shape Pack Specification — Draft 1

A Pack is a versioned semantic capability bundle. It declares name/version, required Shape capabilities, offline behavior, reference assets, licenses/provenance, semantic detectors, validators and generators. Packs MUST NOT silently access the network. Reference assets MUST be checksummed and license-tagged. Pack operations MUST preserve sensitivity and MUST fail rather than guess when semantic/location resolution is ambiguous.
