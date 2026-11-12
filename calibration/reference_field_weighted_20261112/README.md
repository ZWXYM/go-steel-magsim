# Physical H interval sensitivity on the frozen reference curves

`heldout_field_metrics.csv` contains V2/V3/V4 errors for the same eight held-out grade directions and the same digitized reference tables as `calibration/pilot_reference_samples_20261111/`. Original point metrics remain in the CSV. The new metric integrates the squared or absolute residual between piecewise-linear curves, dividing by the actual physical H span. Bands meet at 800 A/m. See `summary.json` for source hashes, covered intervals and largest reference interpolation spans.

The equal-direction mean interval RMSE over the full common H range is 0.034119, 0.031351 and 0.015937 T for V2, V3 and V4, respectively. V4 improves all eight full-range values relative to V2. For B27R090 TD up to 800 A/m, it instead increases the interval RMSE from 0.035865 to 0.052438 T. Sparse parts of a digitized curve rely on linear interpolation. This analysis measures sensitivity to the weighting of existing references; it does not certify independent specimens or change the saved models.

The optional `--field-weighted` flag on `tools/compare_reference_samples.py` reproduces this calculation from a matching saved calibration report and pilot manifest. An independent scalar calculation checked 144 new metrics. The default command still produces the original report format and bytes.
