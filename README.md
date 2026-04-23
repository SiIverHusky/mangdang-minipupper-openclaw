# OpenClaw Voice App (Mini Pupper)

Minimal voice daemon that follows the `minipupper-design.md` loop:

1. Listen with local mic
2. Detect end of turn (RMS threshold + silence timeout)
3. Send transcript text to OpenClaw gateway over HTTP
4. Receive reply text
5. Speak reply with Google TTS
6. Stop playback if user starts speaking (barge-in)

This version is intentionally simple to get a working pipeline quickly.

## Folder

- `openclaw_app.py` - main daemon
- `config.example.yaml` - default config template
- `config.yaml` - your local overrides (create this)
- `requirements.txt` - Python dependencies
- `openclaw.service` - systemd unit template
- `install.sh` - quick dependency installer

## Install

```bash
cd ~/apps-md/openclaw-app
python3 -m pip install -r requirements.txt
```

## Configure

1. Create local config:

```bash
cp config.example.yaml config.yaml
```

1. Edit `config.yaml`:

- Set `google.credentials_path`
- Set `gateway.url` to your OpenClaw endpoint
- Optional: set `gateway.token`
- Set `audio.input_device` to your mic device index or device name
- Set `audio.output_device` to your speaker/headphone device index or device name

For the Mini Pupper hardware used by `ai-app`, the usual mapping is:

- `audio.input_device`: `snd_rpi_simple_card`
- `audio.output_device`: `headphone`

The app accepts either an integer index or a matching device name.

1. Optional env overrides:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_GATEWAY_TOKEN`

## Run

```bash
cd ~/apps-md/openclaw-app
python3 openclaw_app.py
```

## Gateway request format

The app sends:

```json
{
  "text": "user transcript",
  "session": "agent:main:minipupper-talk",
  "source": "minipupper-voice"
}
```

Expected response can be plain text, or JSON with one of these fields:

- `reply`
- `text`
- `output`
- `assistant`
- `message`
- `content`

OpenAI-style `choices[0].message.content` is also supported.

## Status file

Writes state snapshots to `app.status_path` (default `/tmp/minipupper-voice-status.json`).

## Notes

- This app uses RMS-based speech detection (no wake word yet).
- It prioritizes a working end-to-end loop over advanced robustness.
