# Sparse frequency holdout example

`alternate_12_reference_estimates.csv` transcribes the 12 alternate reference values and their standard uncertainties on page 22 of Michal Ulvr's HEFMAG extended Epstein comparison report ([DOI](https://doi.org/10.5281/zenodo.10032756), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). Explicit units and nominal strip configuration columns are supplied in the transcription. The alternate statistics include Lab2's additional strip configuration; they are not an independent specimen or a pool with the page 20 statistics.

Upload this CSV on the frequency validation page and hold out 400 Hz. At each peak J, 200 and 1000 Hz are the only training frequencies. The report gives two transparent comparisons: direct proportional scaling from 200 Hz and interpolation of the per-cycle loss between 200 and 1000 Hz. The three-term fit and its coefficients are left blank. The numerical check in `benchmark.json` is for this table and sample only.
