# Repository instructions

This repository contains the local Blender extension and its worker. It does not contain the website backend.

- Keep the repository private until the owner explicitly requests publication.
- Do not bundle NVIDIA Texture Tools. The setup UI opens NVIDIA's official page and validates the user's installation.
- Preserve sampler/channel semantics, texture formats and external asset names when modifying conversion or export.
- Run the relevant Python tests. Native compression tests require an external NVTT installation.
- Keep user-facing documentation concise. Explain supported behavior and meaningful limits.
- Preserve comparison screenshots unchanged. Layout and captions are editable in `docs/media/comparison.html`; retain the Before / Blender reference / After mapping and the 512 × 512 texture label.
- Keep source changes and release artifacts in sync when shipping an updated add-on.
