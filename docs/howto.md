# How to run the web UI

A short path from zero to an open browser.

## 1. Requirements

- **Python 3.11 or newer**
- **Git**

Check Python:

```bash
python --version
```

## 2. Clone the repository

```bash
git clone https://github.com/theRealAi/drpEditor.git
cd drpEditor
```

## 3. Install

From the project root:

```bash
pip install -e .
```

That installs the `drp` command and the web UI dependencies.

Optional (tests / lint only):

```bash
pip install -e ".[dev]"
```

## 4. Start the server

```bash
drp ui
```

You should see something like:

```text
drp editor UI running at http://127.0.0.1:8765 (Ctrl+C to stop)
```

By default the browser opens for you. If it does not, go to:

**http://127.0.0.1:8765**

Useful variants:

```bash
drp ui MyProject.drp    # open with a project already loaded
drp ui --no-open        # start server only (open the URL yourself)
drp ui --port 9000      # use another port
```

Stop the server with **Ctrl+C** in the terminal.

## 5. Open a `.drp` file in the UI

Once the page loads:

1. Paste a full path to a `.drp` file in the top bar and click **Open**, **or**
2. Click **Upload…**, **or**
3. Drag and drop a `.drp` onto the page.

Then browse timelines/clips in the left nav, click a row to inspect it, edit fields with **Enter**, and use **Save new version** or **Download** when you are done.

**Save new version** writes `YourFile_v2.drp`, `_v3.drp`, … next to the original (it does not overwrite the source).

## Troubleshooting

| Problem | What to try |
| --- | --- |
| `drp` not found | Make sure you ran `pip install -e .` in the same Python/env you are using, then open a new terminal |
| Port already in use | `drp ui --port 9000` |
| Browser did not open | Visit http://127.0.0.1:8765 manually, or use `drp ui --no-open` and open that URL yourself |
| File not found when opening by path | Use the full absolute path to the `.drp` |

For CLI commands, library usage, and reverse-engineering notes, see the [README](../README.md).
