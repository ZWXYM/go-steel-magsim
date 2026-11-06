# Optional low native-weight calibration version

`fixed_h_regularized_transfer_v3` reuses the frozen four-material development data. It adds three beta=0.25 candidates to the original eight candidates and reruns candidate selection using training materials only in each outer material fold. It is an explicit optional model, not the new default. Both versions use the same physical H axis.

Whole-material nested mean RMSE: original V2 0.0440426473 T; V3 0.0308012682 T. Maximum absolute B800 error: V3 0.0603564044 T, so the 0.05 T all-material development target remains unmet. This is retrospective development, not an unseen external validation set. Four materials / eight directions / 208 curve rows / 48 metric rows are not independent new specimens.

`bank.json` and `protocol.json` reproduce V3. The B30P105 child bank excludes that entire material, including reference curves, from candidate selection. Its exported curves match that material's outer fold. Original raw pairs remain in `calibration/pilot_20261003_n8`.

Example (new output directory):

```sh
python tools/predict_calibrated_material.py --bank calibration/regularized_bank_20261106/bank.json --raw-pair calibration/pilot_20261003_n8/B30P105/raw_pair.json --exclude-grade B30P105 --output-dir data/regularized_B30_demo --name Regularized_B30_demo
```

These are B-H-only exports. Loss coefficients remain uncalibrated zero placeholders; they do not support motor efficiency ranking. No new physical solver or training was run for this model version. Full private workpacks and native projects are not included.
