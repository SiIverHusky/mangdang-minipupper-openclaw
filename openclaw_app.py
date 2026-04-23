import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pyaudio
import requests
import sounddevice as sd
import yaml
from dotenv import load_dotenv
from google.cloud import speech
from google.cloud import texttospeech


@dataclass
class GoogleConfig:
    credentials_path: str
    stt_language_code: str
    tts_voice_name: str
    tts_sample_rate_hz: int


@dataclass
class AudioConfig:
    input_device: Optional[str]
    output_device: Optional[str]
    sample_rate_hz: int
    frame_ms: int
    vad_rms_threshold: float
    vad_start_frames: int
    silence_timeout_ms: int
    max_utterance_ms: int


@dataclass
class GatewayConfig:
    url: str
    token: str
    timeout_ms: int
    session: str
    source: str


@dataclass
class AppConfig:
    interrupt_on_speech: bool
    status_path: str
    log_level: str
    playback_barge_in_delay_ms: int


@dataclass
class Config:
    google: GoogleConfig
    audio: AudioConfig
    gateway: GatewayConfig
    app: AppConfig


class AudioRecorder:
    def __init__(self, config: AudioConfig):
        self.config = config
        self._p = pyaudio.PyAudio()
        self._chunk = max(1, int(self.config.sample_rate_hz * self.config.frame_ms / 1000))

    def _resolve_device_index(self, device: Optional[str]) -> Optional[int]:
        if not device:
            return None

        try:
            return int(device)
        except (TypeError, ValueError):
            pass

        wanted = device.strip().lower()
        for index in range(self._p.get_device_count()):
            info = self._p.get_device_info_by_index(index)
            name = str(info.get("name", "")).strip().lower()
            if name == wanted or wanted in name:
                return index

        raise ValueError(f"Could not resolve input device: {device}")

    def close(self) -> None:
        self._p.terminate()

    def _open_input_stream(self):
        kwargs = {
            "format": pyaudio.paInt16,
            "channels": 1,
            "rate": self.config.sample_rate_hz,
            "input": True,
            "frames_per_buffer": self._chunk,
        }

        if self.config.input_device:
            try:
                kwargs["input_device_index"] = self._resolve_device_index(self.config.input_device)
            except ValueError as exc:
                logging.warning("audio_input_device_unresolved=%s error=%s", self.config.input_device, exc)

        return self._p.open(**kwargs)

    @staticmethod
    def _is_speech(frame: bytes, threshold: float) -> bool:
        if not frame:
            return False
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(samples))))
        return rms >= threshold

    def capture_utterance(self) -> bytes:
        stream = self._open_input_stream()
        frames = []
        speech_frames = 0
        silence_frames = 0
        max_frames = int(self.config.max_utterance_ms / self.config.frame_ms)
        silence_limit = int(self.config.silence_timeout_ms / self.config.frame_ms)

        try:
            logging.info("state=LISTENING waiting_for_speech")
            while True:
                frame = stream.read(self._chunk, exception_on_overflow=False)
                if self._is_speech(frame, self.config.vad_rms_threshold):
                    speech_frames += 1
                    frames.append(frame)
                    if speech_frames >= self.config.vad_start_frames:
                        break
                else:
                    speech_frames = 0

            logging.info("state=CAPTURING speech_started")
            while len(frames) < max_frames:
                frame = stream.read(self._chunk, exception_on_overflow=False)
                frames.append(frame)
                if self._is_speech(frame, self.config.vad_rms_threshold):
                    silence_frames = 0
                else:
                    silence_frames += 1
                if silence_frames >= silence_limit:
                    break
        finally:
            stream.stop_stream()
            stream.close()

        return b"".join(frames)

    def detect_speech_onset(self, timeout_ms: int = 200) -> bool:
        stream = self._open_input_stream()
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        speech_frames = 0
        try:
            while time.monotonic() < deadline:
                frame = stream.read(self._chunk, exception_on_overflow=False)
                if self._is_speech(frame, self.config.vad_rms_threshold):
                    speech_frames += 1
                    if speech_frames >= self.config.vad_start_frames:
                        return True
                else:
                    speech_frames = 0
        finally:
            stream.stop_stream()
            stream.close()
        return False


class GatewayClient:
    def __init__(self, config: GatewayConfig):
        self.config = config

    def ask(self, text: str) -> str:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        payload = {
            "model": "openclaw/main",
            "messages": [
                {
                "role": "system",
                "content": (
                    "VOICE MODE (robot speaker): "
                    "Be concise (<=2 sentences) unless the user explicitly asks for long-form. "
                    "No bullet lists. "
                    "If asked for a long story, deliver it in short parts and end with: 'Say continue.'"
                ),
                },
                {"role": "user", "content": text},
            ],
        }

        response = requests.post(
            self.config.url,
            headers=headers,
            json=payload,
            timeout=self.config.timeout_ms / 1000.0,
        )
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            return response.text.strip()

        return self._extract_reply(data)

    @staticmethod
    def _extract_reply(data: Any) -> str:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ["reply", "text", "output", "assistant", "message", "content"]:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message", {})
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, str) and content.strip():
                        return content.strip()
            result = data.get("result")
            if isinstance(result, str) and result.strip():
                return result.strip()
        raise RuntimeError("Gateway response did not include reply text")


class VoiceDaemon:
    def __init__(self, config: Config):
        self.config = config
        self.recorder = AudioRecorder(config.audio)
        self.gateway = GatewayClient(config.gateway)
        self.speech_client = speech.SpeechClient()
        self.tts_client = texttospeech.TextToSpeechClient()

        self.voice = texttospeech.VoiceSelectionParams(
            language_code=config.google.stt_language_code,
            name=config.google.tts_voice_name,
        )
        self.audio_cfg = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=config.google.tts_sample_rate_hz,
        )

    def close(self) -> None:
        self.recorder.close()

    def _write_status(self, state: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "state": state,
            "ts": int(time.time() * 1000),
        }
        if extra:
            payload.update(extra)

        path = self.config.app.status_path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _stt(self, audio_bytes: bytes) -> str:
        recognition_audio = speech.RecognitionAudio(content=audio_bytes)
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.config.audio.sample_rate_hz,
            language_code=self.config.google.stt_language_code,
            enable_automatic_punctuation=True,
        )
        response = self.speech_client.recognize(config=recognition_config, audio=recognition_audio)
        parts = []
        for result in response.results:
            if result.alternatives:
                parts.append(result.alternatives[0].transcript)
        return " ".join(parts).strip()

    def _tts(self, text: str) -> np.ndarray:
        synthesis_input = texttospeech.SynthesisInput(text=text)
        response = self.tts_client.synthesize_speech(
            input=synthesis_input,
            voice=self.voice,
            audio_config=self.audio_cfg,
        )
        pcm16 = np.frombuffer(response.audio_content, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm16

    def _play_with_barge_in(self, pcm: np.ndarray, sample_rate: int) -> bool:
        if pcm.size == 0:
            return False

        stop_playback = threading.Event()
        stop_monitor = threading.Event()
        barge_in_detected = threading.Event()
        play_index = 0
        monitor_start = time.monotonic() + (self.config.app.playback_barge_in_delay_ms / 1000.0)

        def monitor_loop() -> None:
            while time.monotonic() < monitor_start and not stop_monitor.is_set() and not stop_playback.is_set():
                time.sleep(0.02)

            while not stop_monitor.is_set() and not stop_playback.is_set():
                if self.recorder.detect_speech_onset(timeout_ms=200):
                    barge_in_detected.set()
                    stop_playback.set()
                    return

        monitor_thread = None
        if self.config.app.interrupt_on_speech:
            monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
            monitor_thread.start()

        def callback(outdata, frames, _time_info, status):
            nonlocal play_index
            if status:
                logging.debug("audio_status=%s", status)

            if stop_playback.is_set():
                outdata.fill(0)
                raise sd.CallbackStop()

            end = min(play_index + frames, pcm.shape[0])
            chunk = pcm[play_index:end]
            outdata.fill(0)
            outdata[: chunk.shape[0], 0] = chunk
            play_index = end

            if play_index >= pcm.shape[0]:
                raise sd.CallbackStop()

        stream_kwargs: Dict[str, Any] = {
            "samplerate": sample_rate,
            "channels": 1,
            "dtype": "float32",
            "callback": callback,
        }
        if self.config.audio.output_device:
            try:
                device_index = self.recorder._resolve_device_index(self.config.audio.output_device)
                if device_index is not None:
                    stream_kwargs["device"] = device_index
            except ValueError as exc:
                logging.warning("audio_output_device_unresolved=%s error=%s", self.config.audio.output_device, exc)

        try:
            with sd.OutputStream(**stream_kwargs):
                while not stop_playback.is_set() and play_index < pcm.shape[0]:
                    time.sleep(0.02)
        finally:
            stop_monitor.set()
            if monitor_thread is not None:
                monitor_thread.join(timeout=0.5)

        if barge_in_detected.is_set():
            logging.info("state=SPEAKING barged_in=true")
            return True
        return False

    def run(self) -> None:
        logging.info("openclaw_app_started")
        try:
            while True:
                self._write_status("LISTENING")
                audio_bytes = self.recorder.capture_utterance()

                self._write_status("FINALIZING")
                transcript = self._stt(audio_bytes)
                if not transcript:
                    logging.info("state=FINALIZING transcript=empty")
                    continue

                logging.info("state=FINALIZING transcript=%s", transcript)
                self._write_status("THINKING", {"transcript": transcript})

                try:
                    reply = self.gateway.ask(transcript)
                except Exception as exc:
                    logging.error("state=THINKING gateway_error=%s", exc)
                    continue

                if not reply:
                    logging.info("state=THINKING reply=empty")
                    continue

                logging.info("state=THINKING reply=%s", reply)
                self._write_status("SPEAKING", {"reply": reply[:120]})

                pcm = self._tts(reply)
                was_barged = self._play_with_barge_in(
                    pcm=pcm,
                    sample_rate=self.config.google.tts_sample_rate_hz,
                )
                if was_barged:
                    self._write_status("CAPTURING", {"barge_in": True})
        finally:
            self.close()


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")
    return data


def load_config() -> Config:
    load_dotenv(dotenv_path="../.env")

    base_path = os.path.join(os.path.dirname(__file__), "config.example.yaml")
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")

    base_data = _load_yaml(base_path)
    if os.path.exists(config_path):
        override_data = _load_yaml(config_path)
        data = _deep_merge(base_data, override_data)
    else:
        data = base_data

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        data["google"]["credentials_path"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if os.getenv("API_KEY_PATH"):
        data["google"]["credentials_path"] = os.getenv("API_KEY_PATH", "")
    if os.getenv("OPENCLAW_GATEWAY_URL"):
        data["gateway"]["url"] = os.getenv("OPENCLAW_GATEWAY_URL", "")
    if os.getenv("OPENCLAW_GATEWAY_TOKEN"):
        data["gateway"]["token"] = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")

    google_cfg = GoogleConfig(**data["google"])
    audio_cfg = AudioConfig(**data["audio"])
    gateway_cfg = GatewayConfig(**data["gateway"])
    app_cfg = AppConfig(**data["app"])

    if google_cfg.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = google_cfg.credentials_path

    return Config(
        google=google_cfg,
        audio=audio_cfg,
        gateway=gateway_cfg,
        app=app_cfg,
    )


def main() -> None:
    config = load_config()
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=getattr(logging, config.app.log_level.upper(), logging.INFO),
    )

    daemon = VoiceDaemon(config)
    daemon.run()


if __name__ == "__main__":
    main()
