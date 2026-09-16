# Visual re-identification experiment

This isolated experiment will add visual evidence to spatial-temporal handoff
candidates. It does not name people, search a watchlist or turn a camera-local
track ID into a persistent identity by itself.

The first data path is:

```text
track_update.v1 media
 -> source-neutral media catalog
 -> face/body quality gate
 -> local embedding extractor
 -> similarity evidence
 -> fusion with topology and time
 -> provisional global track
```

All processing stays on the local computer. Images and derived embeddings must
follow the same seven-day retention policy. Model weights are versioned
dependencies, not generated event data. Do not commit captured images,
embeddings, credentials or a named face gallery.

## Audit retained media

From the repository root:

```powershell
python experiments/visual-reid/audit_media.py
```

The audit reads the receiver database in read-only mode. It reports whether
each normalized media reference still exists and obtains JPEG dimensions
without decoding or copying the image.

Current source capabilities:

- Dahua supplies semantic `face`, `body` and panoramic roles. Its native face
  crop is the preferred facial input.
- Frigate currently supplies a full `snapshot`. A local detector must find and
  quality-check a face and body crop before embeddings are extracted.
- The tested Reception view is overhead; its latest snapshot shows useful body
  appearance but not a reliable frontal face. Body re-identification is
  therefore required as a fallback for that transition.

## Model boundary

Keep inference behind a provider-neutral interface so the prototype can run on
CPU and a later deployment can benchmark its second GPU. The first model
evaluation will compare explicitly licensed face and person re-identification
models; weights are not downloaded until their exact license and provenance
are recorded.

The pinned manifest records exact official URLs, byte lengths and SHA-384
checksums. Downloaded weights stay outside Git:

```powershell
python experiments/visual-reid/download_models.py
```

Candidate baselines include Intel Open Model Zoo's Apache-2.0
`face-reidentification-retail-0095` and person re-identification models. The
face model requires a tightly aligned frontal crop and produces a 256-value
embedding. OpenCV Zoo is another possible backend, but each model's weight and
training-data terms must be verified independently before product use.

Official references:

- https://github.com/openvinotoolkit/open_model_zoo
- https://github.com/openvinotoolkit/open_model_zoo/tree/master/models/intel/face-reidentification-retail-0095
- https://github.com/opencv/opencv_zoo

## Next validation

1. Confirm the media catalog is complete for Dahua and all Frigate cameras.
2. Add face detection, alignment and an explicit quality result (`usable`,
   `missing`, `too_small`, `blurred`, `profile`, or `occluded`).
3. Add a body-crop extractor for overhead/non-frontal views.
4. Generate ephemeral embeddings for the controlled
   `Dahua -> Reception -> Dahua` tracks.
5. Compare true pairs against known different-person pairs before choosing
   thresholds.
6. Feed calibrated visual scores into candidate fusion; never treat a raw
   similarity threshold as proof of identity.

For controlled, ephemeral body comparisons, use the experiment venv and pass
two or more complete normalized track IDs:

```powershell
experiments\visual-reid\.venv\Scripts\python.exe `
  experiments\visual-reid\compare_tracks.py TRACK_A TRACK_B
```

The command prints pairwise cosine similarities but does not persist vectors.
It refuses to score crops that are too small or have an overhead/horizontal
shape unsupported by the selected upright-person model; unavailable evidence
must remain neutral during later score fusion.

Native Dahua face crops can be aligned and compared with:

```powershell
experiments\visual-reid\.venv\Scripts\python.exe `
  experiments\visual-reid\compare_faces.py TRACK_A TRACK_B
```

This path uses the official five-landmark regressor before creating the face
embedding. A controlled same-person/different-person dataset is still required
before any threshold is selected.

### Initial controlled face baseline

On 2026-09-16, four new Dahua face captures covered two physical people. The
quality gate rejected one side-profile capture. The remaining comparisons were:

- same person, frontal/frontal: cosine similarity `0.639941`;
- different people: `0.083520` and `0.198536`;
- side profile: `unavailable`, rather than a false negative.

This proves that the local extraction path can produce useful separation. It
is not enough data to establish a production identity threshold; calibration
must include more people, poses, lighting conditions and cameras.

Inspect recent handoff candidates with each raw evidence channel kept separate:

```powershell
experiments\visual-reid\.venv\Scripts\python.exe `
  experiments\visual-reid\score_handoffs.py --limit 10
```

This command is read-only and ephemeral. `n/a` means that a modality is
missing or failed its quality gate; score fusion must treat it as neutral.
