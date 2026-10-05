# SVG Converter offline development input

This reviewed subset supplies the existing SVG logging/paste tests and their real import dependencies. AI Core implementation files are needed by imports; no credentials or usage ledger are supplied. Never construct real AI providers or invoke credential helpers during these tests.

Create an external venv and install only `PyQt6>=6.6` and `rich>=13.7.0`, declared by the original requirements. Do not install the entire suite for this task. Use a disposable HOME and TMPDIR, `PYTHONDONTWRITEBYTECODE=1`, `QT_QPA_PLATFORM=offscreen`, and set `STUDY_TOOLS_PYTHON` to the venv Python. From repository root:

```sh
bash _scripts_2/apps/documents/svg_converter/verify.sh quick
```

The unchanged baseline has 18 passing tests. One baseline test characterizes an unhandled log-directory creation error; a bounded repair should replace this expectation and cover OS open rejection. Actual desktop folder opening must remain mocked.

The selected task is log-folder error handling: report directory creation failure and a false `QDesktopServices.openUrl` result without claiming success. Persistent append/clear/copy, provider behavior and real conversion are outside the repair scope.

The manifest lists original source byte identities. A development change intentionally differs from that input; record its separate diff and verification identity. The program catalog is included only because the canonical verifier bootstrap requires its presence, not as a claim that those other programs are included. `run_gui`, branding/assets, CLI and installed macOS operation are outside the acceptance profile. No new license is granted by this input export.
