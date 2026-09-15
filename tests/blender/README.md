# Blender regression scenarios

Run in a disposable Blender scene with Sollumz, the complete PBR2GTA release ZIP, and a validated external NVTT installation. The scripts create materials and objects and inject failures. Do not run them against production data.

Execute `fixture.py` on Blender's main thread (MCP `execute` or the Python console). Optionally supply `TEST_ROOT` as a fresh temporary output directory. It generates its own 512 px PNG inputs. The shared fixture is stored as `pbr2gta_audit` in `sys.modules`.

1. Call `pbr2gta_audit.start('baseline', roots=[pbr2gta_audit.first_model])` and wait for `bpy.context.window_manager.pbr2gta_running` to become false.
2. Execute `run_regressions.py`. Wait for `evidence/regressions_complete.json`; a failure is written to `regressions_failed.json`.
3. Execute `mixed.py`, wait for the worker, then execute `extra.py` and wait for `extra_complete.json`.
4. Set the scene render engine to Cycles. Execute `stress.py` and wait for `cycles_preview_stress_verified.json`. This checks actual native material preview jobs, deferred image deletion and stable image counts over 15 cycles.
5. With no active worker, execute `nvtt_invalid.py`, `nvtt_valid.py`, `nvtt_missing.py`, and `guide_coverage.py` in that order.

Use a fresh fixture/output directory for each full run. Scripts start asynchronous operations; their initial return does not indicate completion. Keep the Blender event loop running. If a test raises, inspect its JSON evidence before advancing. Close the disposable scene without saving after the run.

The ESC regression invokes the public modal handler with an ESC event through MCP; it does not test physical keyboard event delivery. Lifecycle tests invoke the installed load/undo callbacks. File reload and minimum-version installation were additionally checked in isolated Blender 4.2 processes; details are in `reports/FIX_VERIFICATION_0.2.7.md`.
