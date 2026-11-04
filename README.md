![](https://github.com/DanPeled/Synapse/blob/docs/docs/.gitbook/assets/SynapseHeader.png?raw=true)

[![PyPI Downloads](https://static.pepy.tech/badge/synapsefrc)](https://pepy.tech/projects/synapsefrc)

- **Pipeline System:** Organize and manage vision processing tasks efficiently.
- **Multiple Camera Support:** Handle multiple camera inputs simultaneously.
- **NetworkTables Integration:** Post vision results directly to NetworkTables for easy access in FRC dashboards.
- **Global Pipeline Configuration:** Easily configure global pipeline settings.
- **Fast Pipeline Development:** Streamlined workflow for creating and testing new pipelines.
- **Custom Python Pipelines:** Load and run custom Python-based vision pipelines.
- **Realtime Settings:** Adjust settings in real-time through NetworkTables & UI.
- **Dynamic Pipeline Creation:** Create and modify pipelines dynamically.
- **Interactive UI:** A responsive, visual interface for:
  - Viewing live camera feeds
  - Managing and switching between pipelines
  - Tuning pipeline parameters in real-time
  - Monitoring output data and debugging visually
    Designed for intuitive interaction and minimal setup.

[Documentation](https://danpeled.gitbook.io/synapse/)
[Discord Server](https://discord.gg/zHJRV8Stj3)

## Project Structure

- `synapse_core`: Contains the core Python library for Synapse.
- `synapse_net`: Contains the networking library for the runtime, including NetworkTables and socket communication, as well as Protocol Buffers message definitions.
- `synapse_lib` Contains the vendordep library for the robot to communicate with the Synapse runtime.
- `synapse_ui`: Contains the graphical user interface (UI) for interacting with the Synapse runtime.
- `synapse_imager`: Builds release images for coprocessors with the runtime and a sample project preinstalled.

## Linux images

The [Linux image builder](image_builder/README.md) creates Synapse images from
the latest supported Armbian Debian CLI minimal releases for Raspberry Pi 4/5,
Radxa Dragon Q6A, Orange Pi 5 and Orange Pi 5 Plus. The **Synapse Linux images**
GitHub Actions workflow builds selected boards on demand.

## JavaScript development

Use Bun 1.4.4, pinned in `.bun-version` and each frontend's `packageManager`.
In `synapse_ui`, `synapse_sim`, or `synapse_manager`, run
`bun install --frozen-lockfile` followed by `bun run build` or `bun run dev`.
The frontend scripts explicitly use Bun's runtime; a separate Node.js or npm
installation is not required for these project commands. Bun text lockfiles
replace the previous npm and pnpm lockfiles.

CI follows [Bun's official GitHub Actions guide](https://bun.com/guides/runtime/cicd)
using `oven-sh/setup-bun@v2`. GitHub's JavaScript actions retain their own
GitHub-managed runtime; their versions have been updated to use Node.js 24
instead of the deprecated Node.js 20 runtime.

Pi 4 and Pi 5 use one shared `rpi4b` image, as declared in
[Armbian's official reusable-board configuration](https://github.com/armbian/armbian.github.io/blob/main/release-targets/reusable.yml).
The default image workflow builds four artifacts covering the five boards.
Selecting Pi 5 explicitly resolves to the same shared Pi artifact.

## Workflow scheduling

Build and test workflows remain automatic. Superseded validation runs are
cancelled when a newer commit arrives on the same branch or pull request.
PR labeling, development-library publishing, and PyPI publishing are manual-only
under Actions. The labeler requires a pull request number. Tagged desktop
manager builds and manually requested releases retain their existing triggers.
Image builds run when their application, dependency, or builder inputs change.

## License

This project is licensed under the [GNU V3 License](LICENSE).
