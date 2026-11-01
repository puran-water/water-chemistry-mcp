# Known issues

## Complete chemistry regression qualification

The earlier broader source suites retained failed, skipped and incomplete cases. The public smoke tests cover only their stated cases and do not close every chemistry regression.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

Historical native-source snapshot: 506 passed and 32 setup errors. Those counts predate this cleaned public export. Later repairs exercised subsets only; TEST-RESULTS.md reports the separate installed-artifact qualification. The full source suite has not been repeated against the standalone public package.

## Qualify native PHREEQC installation across platforms

The public artifact does not bundle a newly qualified native runtime for every OS and architecture. Native-library loading and chemistry parity need platform-specific validation.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Qualify additional redistributable databases

Only databases with retained source and notice records are bundled. Other historical database variants are excluded rather than silently substituted.

This is an acknowledged v0.1.0 limitation. See README.md and TEST-RESULTS.md for scope and evidence.

## Repair optional bulk database downloader import

Static review finds the optional bulk downloader references concurrent.futures without importing concurrent. The bundled-library speciation path passes its recorded checks. External database downloading was not qualified in this release.
