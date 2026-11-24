[README.md](https://github.com/user-attachments/files/35310512/README.md)
# Jarvis — offline voice macros for Windows

Jarvis listens to your microphone **fully offline** (using [Vosk](https://alphacephei.com/vosk/)), waits for a wake word (by default **"Джарвис"** / "Jarvis"), and runs the macro that matches the phrase you say next.

Macros are created in a simple GUI: you pick activation phrases and build a sequence of actions (launch files, open websites, click, type text, press hotkeys, wait, run commands). No coding required.

> **The speech model is not included in this repository.** You choose and download the Vosk model for your own language (see [Installation](#installation)).

## Features

- Offline speech recognition with any Vosk language model
- Wake word + command, e.g. *"Джарвис, открой блокнот"*
- GUI to create, view, edit, run and delete macros
- Action types:

| Action | What it does |
|---|---|
| Launch file / program | Starts any file or program, optionally with arguments |
| Open website | Opens a URL in the default browser |
| Click at coordinates | Left / right / middle click, single or double. Coordinates are picked with a full-screen overlay |
| Click on image | Finds a PNG on the screen and clicks its center |
| Type text | Pastes text via the clipboard (Unicode-safe), optionally presses Enter |
| Press keys | Hotkeys such as `win+d`, `ctrl+shift+esc`, `alt+f4` |
| Wait | Pauses for a given number of seconds |
| Run command | Runs a `cmd` command, e.g. `shutdown /s /t 0` |

- Configurable default delay between actions
- Macros are stored as plain JSON files in the `macros/` folder

## Requirements

- Windows 10 / 11
- Python 3.9 or newer (with `tkinter`, included in the standard python.org installer)
- A microphone (the default system input device is used)
- Administrator rights. The script asks for them automatically (UAC prompt) so that clicks and hotkeys also work in elevated windows

## Installation

1. Clone the repository and open the folder:

   ```bash
   git clone https://github.com/<your-username>/<your-repo>.git
   cd <your-repo>
   ```

2. (Recommended) Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. **Download a Vosk model for your language** from <https://alphacephei.com/vosk/models>.
   - Small models (about 40–50 MB) are fast and good enough to start with.
   - Larger models are more accurate but heavier and slower to load.

5. Unpack the model so that the folder is called `model` and sits next to `jarvis.py`:

   ```
   <your-repo>/
   ├── jarvis.py
   ├── requirements.txt
   ├── model/              <- the Vosk model you downloaded (contains am/, conf/, graph/ ...)
   └── macros/             <- created automatically
   ```

   The script also finds a folder named `vosk-model-*` next to it, but make sure the model's files are not nested one level too deep (`model/am`, not `model/some-folder/am`).

6. **Set the wake word for your language.** The default wake words in `jarvis.py` are Russian spellings of "Jarvis". If your model is not Russian, open `jarvis.py` and edit the `WAKE_WORDS` list to a word your model recognizes, for example:

   ```python
   WAKE_WORDS = ["jarvis"]
   ```

   Run the script, say the wake word, and check the log (see below) to see how the model actually spells it. Add every variant you see.

## Usage

Run the script:

```bash
python jarvis.py
```

Confirm the UAC prompt. The main menu opens:

- **Activate control**: starts listening. The window shows a live log, including what the recognizer heard (`Услышал: ...`).
- **Create macro**: opens the macro editor.
- **My macros**: view, edit, run and delete macros.
- **Exit**

> The interface texts and the `Услышал:` log prefix are in Russian. The examples in this README use Russian phrases, but your phrases should be in the language of the model you installed.

### Running macros by voice

Click **Activate control** and say the wake word followed by an activation phrase:

> "Джарвис, открой блокнот"

If you say only the wake word, Jarvis plays a beep and waits 6 seconds for the phrase.

Matching rules:

- A macro fires when one of its phrases appears in the spoken command as whole words.
- If several macros match, the one with the **longest** phrase wins.
- Use specific phrases. A very short phrase such as "google" would also fire on "search google for ...".

### Creating a macro

1. Click **Create macro**.
2. Enter a **name**.
3. Add one or more **activation phrases** to the table (what you say after the wake word).
4. Set the **default delay** between actions (seconds).
5. Build the action list with **Add**. You can edit an action by double-clicking it, and delete it or reorder it with the ↑ / ↓ buttons.
6. Click **Save macro**.

Tips:

- Write phrases the way the model *hears* them. Check the `Услышал: ...` lines in the control window and copy the exact wording. Small models sometimes mishear words.
- For **Click at coordinates**, press the point-selection button, then click the desired spot on the darkened screen (Esc cancels).
- For **Click on image**, save a cropped screenshot (PNG) of the button or icon. It must look the same as on screen (same theme and scaling). The script waits up to the timeout you set and aborts the macro if the image is not found.
- The default delay is applied between actions, but not after a *Wait* action and not after the last action.

### Managing macros

Open **My macros** to see all macros, their phrases and steps. From there you can **edit**, **run** (windows hide and the macro starts after 3 seconds) or **delete** a macro.

### Macro file format

Each macro is a JSON file in `macros/`, for example:

```json
{
  "name": "Open notepad and type",
  "phrases": ["открой блокнот"],
  "default_delay": 0.5,
  "actions": [
    { "type": "launch", "path": "C:\\Windows\\System32\\notepad.exe", "args": "" },
    { "type": "wait", "seconds": 2 },
    { "type": "type", "text": "Hello!", "enter": false }
  ]
}
```

Action types: `launch`, `url`, `click`, `click_image`, `type`, `keys`, `wait`, `cmd`. Mouse button values in `click` actions are stored in Russian (`левая`, `правая`, `средняя`). It is easiest to create and edit macros through the GUI.

## Configuration

Constants at the top of `jarvis.py`:

| Constant | Meaning |
|---|---|
| `WAKE_WORDS` | Accepted spellings of the wake word. Must match how your model writes it |
| `ARMED_SECONDS` | How long Jarvis waits for a phrase after hearing only the wake word |
| `SAMPLE_RATE` | Audio sample rate (16000 for Vosk models) |

## Safety notes

- Macros can launch programs and run arbitrary `cmd` commands. **Only use macros you created or trust**, and don't import macro files from unknown sources.
- Moving the mouse to the top-left corner of the screen aborts a running macro (`pyautogui` fail-safe).
- Recognition is not perfect. Avoid assigning destructive commands (for example, shutdown) to short or easily confused phrases.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'sounddevice'` (or another module) | The packages were installed into a different Python. Run `python -m pip install -r requirements.txt` with the same interpreter / virtual environment you use to run the script |
| "Модель Vosk не найдена" in the log (model not found) | Check that the `model` folder contains `am`, `conf`, `graph` directly (no extra nested folder) |
| Wake word is not recognized | Look at the `Услышал: ...` log lines and add the heard variant to `WAKE_WORDS` |
| Nothing is heard | Make sure the right microphone is set as the Windows default input device |
| Image click never finds the image | Re-take the screenshot at the current screen scaling and theme |
| Macro does nothing after a click-by-coordinates step | Re-pick the point. Coordinates depend on resolution and window position |

## Repository contents

```
jarvis.py          the whole application
requirements.txt   Python dependencies
README.md          this file
.gitignore         keeps the model, macros and virtual environment out of git
```

Suggested `.gitignore`:

```
.venv/
__pycache__/
model/
macros/
```

The Vosk model is large and personal macros may contain local paths, so neither should be committed.
