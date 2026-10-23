# CI test checkpoints

Firmware/configuration changes run both Legacy and Clique builds. These are the only
existing validation commands, and shared keymap/Kconfig/west inputs can affect both
halves and both variants. Deferring one would leave firmware untested; there is no
independent inexpensive test suite to substitute. Unknown paths, dependencies, build
scripts and workflow edits also run both builds. README, changelog, license and Markdown
under docs skip firmware builds on PRs.

Ready-for-review firmware changes, V3.0 integration, published releases, manual
dispatch, tag pushes and Monday 04:23 UTC build both variants. Feature pushes no longer
duplicate PR builds; superseded PR runs are cancelled. Existing job names, container
image, toolchain, artifact names and commands remain. This workflow uploads firmware
artifacts and does not publish releases. Dispatch build.yml to build all firmware
manually.

CI policy changes are checked locally with selector fixtures and actionlint. Application
builds, database services, image publication and remote workflow dispatches are not run
to validate workflow source edits. Full runtime validation occurs after delivery.

A PR opened ready for review also runs the complete checks. Root Markdown and nested
README/changelog files are documentation-only inputs on PRs.
