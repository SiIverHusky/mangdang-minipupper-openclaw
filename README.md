# OpenClaw Voice App for Mini Pupper

This app runs on the robot and handles the full voice loop locally:

1. Capture microphone audio
2. Detect speech with RMS-based VAD
3. Send the transcript to a remote OpenClaw gateway
4. Receive assistant text back
5. Speak the reply with Google TTS
6. Stop playback when the user starts speaking again

It is designed for anyone with a Mini Pupper and a remote gateway who wants a simple end-to-end voice setup.

## What you need

- A Mini Pupper with working mic and speaker output
- A Google Cloud service-account JSON file with Speech-to-Text and Text-to-Speech enabled
- A reachable OpenClaw gateway URL
- A bearer token for the gateway if it is protected

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

- Set `google.credentials_path` to your service-account JSON file
- Set `gateway.url` to the remote OpenClaw gateway endpoint
- Set `gateway.token` if the gateway requires auth
- Set `audio.input_device` to your mic device index or device name
- Set `audio.output_device` to your speaker/headphone device index or device name
- Leave `interrupt_on_speech` enabled if you want barge-in behavior

For the Mini Pupper hardware used by `ai-app`, the usual mapping is:

- `audio.input_device`: `snd_rpi_simple_card`
- `audio.output_device`: `headphone`

The app accepts either an integer index or a matching device name.

If you are unsure about the device names on your robot, list them with a small Python snippet or use the values shown by `sounddevice` / `pyaudio` on the device.

1. Optional env overrides:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_GATEWAY_TOKEN`

The config file is the preferred place to set these values. Environment variables are useful for testing or deployment.

## Remote gateway setup

If the gateway runs on another machine, point `gateway.url` at the full HTTP endpoint, for example:

```yaml
gateway:
  url: "https://your-gateway.example.com/v1/chat/completions"
  token: "your-token-if-needed"
```

The app sends requests with:

```json
{
  "model": "openclaw/main",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

and includes:

```http
Authorization: Bearer <gateway token>
```

That means a 400 error is usually a payload/model problem, not an auth problem.

## Run

```bash
cd ~/apps-md/openclaw-app
python3 openclaw_app.py
```

## Gateway request format

The app sends OpenAI-style chat-completions JSON:

```json
{
  "model": "openclaw/main",
  "messages": [
    {
      "role": "user",
      "content": "user transcript"
    }
  ]
}
```

The gateway response can be plain text or JSON. The app accepts these fields:

- `reply`
- `text`
- `output`
- `assistant`
- `message`
- `content`

OpenAI-style `choices[0].message.content` is also supported.

## Audio notes

- Input uses 16 kHz mono PCM capture.
- Output uses Google TTS at the configured sample rate.
- `playback_barge_in_delay_ms` adds a short grace period before barge-in monitoring starts, which helps prevent the app from hearing its own first syllables.
- If the speaker still feeds back into the mic, increase `playback_barge_in_delay_ms` or temporarily set `interrupt_on_speech: false`.

## Troubleshooting

- If the app cannot hear you, check `audio.input_device` first.
- If playback is silent, check `audio.output_device` and the robot's audio routing.
- If you get a 400 from the gateway, verify the `model` and `messages` fields.
- If you get a 401, check the bearer token.
- If the app keeps interrupting itself, increase the playback barge-in delay.

## Status file

Writes state snapshots to `app.status_path` (default `/tmp/minipupper-voice-status.json`).

## Notes

- This app uses RMS-based speech detection (no wake word yet).
- It prioritizes a working end-to-end loop over advanced robustness.
