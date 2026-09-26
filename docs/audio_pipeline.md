# Audio pipeline — Phase 1

Local selected input and authenticated phone input converge in `AudioPipeline`.
The sounddevice callback copies PCM/status and uses a bounded `CaptureBridge`;
the loop runs eligibility, the existing amplitude idle gate, safe enqueue and
pre-send revalidation. Phone has no separate relay/queue or duplicate gates.
Phase 2 adds optional local WebRTC VAD. There is still no speaker verification,
enrollment or identity model loading in this layer.

`AudioFrame.timestamp` uses the pipeline's injected monotonic clock. For phone,
this is arrival time on the host, not a synchronized phone capture timestamp.
It detects host backlog, not audio buffered on the phone before transmission.
Frames must be mono PCM16, 16 kHz, even-sized and at most 8192 bytes (the current
phone ScriptProcessor uses 4096 samples). PCM contents are not rewritten.

Policy defaults live in `AudioPolicy`; overrides use existing `config/voice.json`:

| Key | Default |
| --- | ---: |
| audio_max_age_seconds | 1.0 |
| audio_playback_hold_seconds | 0.25 |
| audio_pre_roll_seconds | 0.512 |
| audio_queue_frames | 32 |
| audio_diagnostic_interval_seconds | 5.0 |
| idle_mic_after_seconds | 180 |
| idle_wake_level | 0.055 |
| audio_vad_enabled | true in KIRA defaults; false in transport-only tests |
| audio_vad_aggressiveness | 2 |
| audio_vad_voiced_ratio | 0.30 |
| audio_vad_hangover_seconds | 0.7 |

Both sources obey mute, playback and sleep when wake-word mode is enabled. Only
otherwise eligible sleeping audio feeds the existing local wake detector.

When enabled, VAD splits each 16 kHz mono PCM frame into 20 ms WebRTC windows.
The adapter keeps incomplete tails per `stream_id` and detects voice when
at least 30% of its complete windows are voiced. Eligible onset audio is retained
in bounded pre-roll; 700 ms of natural pauses after detected speech continue to
Gemini so pauses do not repeatedly end the audio stream. Ordinary VAD silence is
not a transport discontinuity. This is speech activity detection only; it does not identify the
speaker and does not transcribe audio. VAD is never called from sounddevice's
callback. If the optional package cannot load or fails during inference, the
pipeline reports degraded operation and retains the standard audio gates. It
does not prevent startup or silently discard every utterance. `OFF` is available
by setting `audio_vad_enabled` to false.
Playback tokens cover Gemini, actual hardware writes and Edge/afplay. Overlapping
players cannot release each other's gate. The monotonic hold applies after output
ends; capture from before/during that interval cannot be replayed afterward.
Pre-roll retains only eligible idle audio and keeps original timestamps.

Expiry is checked on admission AND immediately before sending. Queue saturation,
PortAudio status and rejected frames record gaps/counters with rate-limited logs.
A gap is carried on the next accepted frame; the Gemini adapter sends
`audio_stream_end` before resuming PCM after a discontinuity. This can end a turn;
it intentionally avoids presenting missing/changed-source audio as uninterrupted.

## Source authority

The latest authenticated phone connection takes authority at connection start.
Only lifecycle events can change it. Old frames (including queued ones) fail
pre-send validation; closing an old socket cannot close its successor. After the
active socket closes, no older socket regains ownership; local input resumes.
A connected silent phone retains authority until it disconnects (no automatic
timeout/failover policy). Session teardown detaches transport and reconnect
invalidates audio predating that session.

`origin`, `stable_device_id` and `stream_id` are separate. Remote keeps the existing
fingerprint of its persistent pairing key, not its connection token or stream id.
Re-pairing changes that identity. It identifies a paired browser installation,
not a guaranteed physical microphone. Local selection fingerprints actual device
name and host API, excluding PortAudio index. Identical names or renamed devices
remain a limitation; unavailable metadata yields no fabricated stable identity.

Explicit `System default` opens PortAudio default. A named input requires an
exact match; disappearance/open failure never substitutes default. Runtime stream
inactivity reports unavailability and leaves Remote usable. Silent hardware that
continues reporting an active stream cannot be diagnosed without further policy.

## Validation

`python3 -m unittest discover -s tests -p 'test_*.py'` includes audio tests, with
fake clocks, queues, subprocesses and production entrypoint harnesses. No real
capture, afplay, network, profile or account is used.

`python3 -m tests.benchmark_audio_pipeline` runs 200,000 iterations x 5 repetitions
per case. It measures Python metadata/gate overhead only, not actual sounddevice,
Gemini or end-to-end audio performance.
