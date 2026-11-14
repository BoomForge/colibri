# Milo bundled Colibri — integrated Windows portable preview

**Source of truth:** this fork (`BoomForge/colibri`), development branch
`feature/milo-desktop-core-20261114`. The build workflow compiles Colibri
engines directly from the same Git commit before packaging `Milo.exe`.

## Single-download layout

After a successful run of
[the integrated Windows workflow](https://github.com/BoomForge/colibri/actions/workflows/milo-integrated-windows.yml),
the portable ZIP contains:

```text
Milo.exe
_internal/                 # bundled Python runtime used by Milo.exe
Runtime/
    openai_server.py       # Colibri HTTP gateway, from this fork
    family_registry.py     # Colibri model-family routing, from this fork
    qwen36.exe             # Qwen3.6 / Qwen3 GGUF engine; built with Vulkan
    colibri.exe
    ...                    # all supported Windows family engines
    shaders/*.spv          # compiled Vulkan shaders
    LICENSE-colibri.txt
    BUILD-SOURCE.txt       # provenance/commit ID
Models/
    README-MODELS.txt
    <checkpoint folder>/
        config.json
        tokenizer.json
        *.gguf / *.safetensors / *.bin
```

No external Colibri install, Python install, terminal, npm, virtual machine,
website, or separate engine download is needed to RUN **this application**.
The user must still provide actual model weights because the source upload
and GitHub repository do not contain them, and models can be several gigabytes.

## First-run behaviour

1. Extract the ZIP fully; `Milo.exe` depends on the included `Runtime`,
   `_internal` and `Models` folders. Do not launch the exe alone.
2. Open `Milo.exe`. It creates or finds `Models` alongside itself.
   If the install directory is not writable (e.g. Program Files), Milo
   transparently uses `%LOCALAPPDATA%\Milo\Models` instead.
3. Use **Models > Import existing model** to select the FULL existing Colibri
   checkpoint directory. This copies, never removes, your existing weights
   into the managed `Models` folder. Large transfers take disk space.
   Alternatively put compatible checkpoint subfolders in `Models` yourself.
4. Select an installed model. Milo starts the matching first-party Colibri
   engine when the app opens, bound only to `127.0.0.1` on a private,
   ephemeral port. The process is launched without a terminal window.
5. Once the model is loaded, Milo's indicator changes to
   **Bundled Colibri** and chat is available. If startup fails, open the
   **Models > View Colibri log** action.

The previously imported private conversational Milo persona, chat histories
and memories live separately in `%LOCALAPPDATA%\Milo` and are not replaced.

## Safety and process ownership

- The embedded gateway and the C engine are built from the current fork,
  not downloaded from arbitrary third-party release endpoints.
- `Milo.exe` starts itself in hidden worker mode with `CREATE_NO_WINDOW`.
  The engine child also uses `CREATE_NO_WINDOW` only when
  `MILO_HIDE_ENGINE_CONSOLE=1`; normal Colibri CLI behaviour is unchanged.
- The spawned worker monitors the Milo UI process and exits when its owner
  exits. The Colibri engine is attached to its worker's Windows job object.
- Closing Milo terminates only the child worker that Milo started. It must
  not terminate an unrelated installed Colibri service.
- No model data is uploaded or downloaded without the user selecting it.
  No Windows service, scheduled task, elevated privilege or firewall opening
  is installed. The gateway uses a local loopback port only.
- Source folders are copied to managed `Models` on explicit user action,
  with source symlinks rejected and available disk space checked.

## Verification limits

CI checks Python source compilation, local store and backup tests,
the bundled first-party gateway import, and that the Windows engines
and Vulkan shaders are present. It does **not** prove that every potential
model family works or that the user's particular Qwen3-4B checkpoints,
GPU drivers, and prompt format have completed an end-to-end chat.

The ZIP is unsigned and is a preview. Do not disable Windows Smart App
Control to bypass a block: use the source installation or a later signed build.
