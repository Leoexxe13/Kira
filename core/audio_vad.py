"""Small offline WebRTC VAD adapter for 16 kHz mono PCM16 audio."""
from __future__ import annotations


class WebRtcVoiceActivity:
    """Frame adapter: AudioPipeline chunks are split into valid 20 ms VAD frames."""

    def __init__(self, aggressiveness: int = 2, voiced_ratio: float = 0.30):
        if not 0 <= int(aggressiveness) <= 3:
            raise ValueError("WebRTC aggressiveness must be 0..3")
        if not 0.0 <= float(voiced_ratio) <= 1.0:
            raise ValueError("voiced_ratio must be 0..1")
        try:
            import webrtcvad
        except ImportError as exc:
            raise RuntimeError("webrtcvad is not installed") from exc
        self._vad = webrtcvad.Vad(int(aggressiveness))
        self.voiced_ratio = float(voiced_ratio)
        self._buffers: dict[str, bytearray] = {}

    def reset(self, stream_id: str | None = None) -> None:
        if stream_id is None:
            self._buffers.clear()
        else:
            self._buffers.pop(stream_id, None)

    def accepts(self, stream_id: str, pcm: bytes) -> bool:
        """Return whether this chunk contains enough voiced 20 ms subframes.

        Incomplete tails remain buffered per stream and are never sent to VAD.
        A chunk with no complete subframe is conservatively rejected.
        """
        buf = self._buffers.setdefault(stream_id, bytearray())
        buf.extend(pcm)
        frame_bytes = 16000 * 20 // 1000 * 2
        voiced = total = 0
        while len(buf) >= frame_bytes:
            subframe = bytes(buf[:frame_bytes])
            del buf[:frame_bytes]
            total += 1
            # Propagate errors so the core can visibly disable optional VAD,
            # rather than silently rejecting every utterance forever.
            voiced += int(self._vad.is_speech(subframe, 16000))
        return bool(total and voiced / total >= self.voiced_ratio)
