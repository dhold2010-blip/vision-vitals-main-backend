# Experimental camera estimates v1

POST `/api/v1/analyses/{id}/camera-measurements` requires the report owner's user
session. It computes a new pulse or face estimate and merges it under
`result.optical_metrics`; the other modality is preserved under a database row
lock. A rejected measurement replaces that modality with null and a retry reason.
Migration 0007 adds a nullable JSON column; old reports are compatible. Raw video,
colour samples and landmarks are not retained by this endpoint or sent to Gemini.
These inputs are browser supplied, not cryptographically attested.

Pulse uses a separate 30-second fingertip-over-rear-camera capture. Green channel
means use actual unique-frame timestamps, discard 3 seconds for settling, and
resample to 20 Hz. Linear detrending, a Hann-window spectrum, peak separation,
autocorrelation and two-window consistency reject weak/ambiguous signals. The
engineering acceptance range is 45–180 bpm. Missing coverage, excessive exposure,
frame gaps, short duration, or disagreement produce no number. Coverage is only a
colour heuristic. Motion, lighting flicker, camera processing and pulse harmonics
can still create false estimates; signal gates are not proof of accuracy. Not for
arrhythmia detection, medical monitoring or treatment decisions. No confidence
percentage or clinical accuracy is claimed. Device/skin-tone/lighting validation
against a reference instrument is still required, including rejection rates.

Face uses the pinned MediaPipe Face Landmarker revision 1 in the browser. It
requires one face, adequate size and image sharpness, neutral expression, and
forward head orientation (within 15 degrees by model transform). Six paired
landmarks (33/263,133/362,61/291,234/454,127/356,172/397) are reflected about the
forehead-to-chin midline (10/152). Mean reflected-pair distance divided by outer-eye
span is reported as percent eye span, not an arbitrary 0–100 beauty/health score.
The method aligns in-plane roll but cannot remove perspective, occlusions,
expression effects or model bias. There is no calibrated anatomical measurement.

Tests cover synthetic rates, noise, harmonics, timing gaps, jitter, signal drift,
missing coverage, face transforms, reflection geometry, input limits, ownership,
persistence, merging and failed remeasurement. Synthetic recovery does not
validate physiological accuracy. Browser fixture checks verify model loading and
landmarks but do not replace live-phone validation.

Primary technical references (algorithm here is independent, not the validated
Google implementation):
- https://pmc.ncbi.nlm.nih.gov/articles/PMC9053269/
- https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/web_js
