import numpy as np
import matplotlib.pyplot as plt
from scipy import integrate
from scipy.interpolate import interp1d
from scipy.ndimage import gaussian_filter1d

plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def read_mumax_data(filename):
    """Read MuMax3 output table data"""
    data = np.loadtxt(filename, skiprows=1)
    return data


def extract_magnetic_properties(data, Msat=1.56e6):
    """Extract magnetic properties with COMPLETE permeability curves

    Args:
        data: MuMax3 output table
        Msat: Saturation magnetization in A/m (default: 1.56e6 for Fe-3%Si, Moses 2012)
    """

    # Extract columns
    mx, my, mz = data[:, 1], data[:, 2], data[:, 3]
    B_ext_x, B_ext_y, B_ext_z = data[:, 4], data[:, 5], data[:, 6]  # External field!
    E_total = data[:, 10]

    # Calculate magnetization magnitude
    M_mag = np.sqrt(mx ** 2 + my ** 2 + mz ** 2)

    # Calculate applied field H (A/m) from B_ext
    mu0 = 4 * np.pi * 1e-7
    Hx = B_ext_x / mu0
    Hy = B_ext_y / mu0
    Hz = B_ext_z / mu0

    # === KEY FIX: Calculate TOTAL B = mu0*(H + M*Msat) ===
    # MuMax3 outputs normalized M, need to scale by Msat
    Mx_real = mx * Msat  # Real magnetization in A/m
    My_real = my * Msat
    Mz_real = mz * Msat

    # Total magnetic induction
    Bx_total = mu0 * (Hx + Mx_real)
    By_total = mu0 * (Hy + My_real)
    Bz_total = mu0 * (Hz + Mz_real)

    print(f"\n=== Material Parameters ===")
    print(f"Msat = {Msat:.2e} A/m")
    print(f"Max |M_real| = {np.max(M_mag) * Msat:.2e} A/m")

    # === Use SIGNED field strength ===
    # Find reference direction (first non-zero field)
    H_vec = np.column_stack([Hx, Hy, Hz])
    for i in range(len(Hx)):
        H_ref_mag = np.sqrt(Hx[i] ** 2 + Hy[i] ** 2 + Hz[i] ** 2)
        if H_ref_mag > 100:
            H_ref_dir = np.array([Hx[i], Hy[i], Hz[i]]) / H_ref_mag
            break
    else:
        H_ref_dir = np.array([1, 0, 0])

    print(f"\n=== Reference Field Direction ===")
    print(f"H_ref_dir = ({H_ref_dir[0]:.4f}, {H_ref_dir[1]:.4f}, {H_ref_dir[2]:.4f})")

    # Signed projections
    H_signed = Hx * H_ref_dir[0] + Hy * H_ref_dir[1] + Hz * H_ref_dir[2]
    M_signed = (Mx_real * H_ref_dir[0] + My_real * H_ref_dir[1] +
                Mz_real * H_ref_dir[2])  # In A/m
    B_signed = (Bx_total * H_ref_dir[0] + By_total * H_ref_dir[1] +
                Bz_total * H_ref_dir[2])  # In T

    # Also calculate magnitudes for reference
    B_mag_total = np.sqrt(Bx_total ** 2 + By_total ** 2 + Bz_total ** 2)

    print(f"\n=== Field and Magnetization Analysis ===")
    print(f"H_signed range: {H_signed.min():.1f} to {H_signed.max():.1f} A/m")
    print(f"M_signed range: {M_signed.min():.2e} to {M_signed.max():.2e} A/m")
    print(f"B_total range: {B_signed.min():.4f} to {B_signed.max():.4f} T")
    print(f"|M| range: {M_mag.min():.6f} to {M_mag.max():.6f} (normalized)")

    # Use signed values for analysis
    H_primary = H_signed
    M_primary = M_signed  # Now in A/m, not normalized!
    B_primary = B_signed

    # Identify turning points - IMPROVED METHOD
    # Find: max H, min H, and zero crossings
    turning_points = [0]

    # Method 1: Find extreme values and zeros
    idx_max_H = np.argmax(H_primary)
    idx_min_H = np.argmin(H_primary)

    # Find zero crossings
    zero_crossings = []
    for i in range(len(H_primary) - 1):
        if H_primary[i] * H_primary[i + 1] < 0:  # Sign change
            zero_crossings.append(i)

    # Combine all critical points
    critical_points = sorted(set([0, idx_max_H, idx_min_H] + zero_crossings + [len(H_primary) - 1]))
    turning_points = critical_points

    print("\n=== Hysteresis Loop Segments ===")
    for i, idx in enumerate(turning_points):
        print(f"Point {i}: index={idx}, H={H_primary[idx] / 1000:.2f} kA/m, "
              f"M={M_primary[idx] / 1e6:.4f} MA/m, |M|={M_mag[idx]:.4f}")

    # === FIX 1: Correct Ms_real calculation ===
    Ms_real = np.max(M_mag) * Msat  # Real saturation in A/m (FIXED!)

    # Remanence (at H=0)
    zero_H_indices = []
    for i in range(len(turning_points) - 1):
        seg_start, seg_end = turning_points[i], turning_points[i + 1]
        H_seg = H_primary[seg_start:seg_end + 1]
        if np.min(H_seg) * np.max(H_seg) <= 0:
            idx_in_seg = np.argmin(np.abs(H_seg))
            zero_H_indices.append(seg_start + idx_in_seg)

    if len(zero_H_indices) >= 2:
        Mr_values_real = M_primary[zero_H_indices]  # In A/m
        Mr_real = np.mean(np.abs(Mr_values_real))
        print(f"\nRemanence found at indices: {zero_H_indices}")
        print(f"Mr values: {Mr_values_real / 1e6} MA/m")
    else:
        zero_mask = np.abs(H_primary) < 100
        Mr_real = np.mean(np.abs(M_primary[zero_mask])) if np.sum(zero_mask) > 0 else 0

    # Coercivity (where M=0)
    Hc_values = []
    for i in range(len(turning_points) - 1):
        seg_start, seg_end = turning_points[i], turning_points[i + 1]
        M_seg = M_primary[seg_start:seg_end + 1]
        H_seg = H_primary[seg_start:seg_end + 1]
        sign_changes = np.where(np.diff(np.sign(M_seg)))[0]

        for idx in sign_changes:
            if idx < len(M_seg) - 1:
                M1, M2 = M_seg[idx], M_seg[idx + 1]
                H1, H2 = H_seg[idx], H_seg[idx + 1]
                if abs(M2 - M1) > 1e-6:
                    H_coer = H1 - M1 * (H2 - H1) / (M2 - M1)
                    Hc_values.append(abs(H_coer))
                    print(f"\nCoercivity at segment {i}: Hc = {abs(H_coer):.2f} A/m")

    Hc = np.mean(Hc_values) if len(Hc_values) > 0 else 0

    # === Calculate COMPLETE permeability curve ===
    # Extract INITIAL magnetization curve (monotonic H increase from near zero)

    # Find the best segment for initial curve (H: low → high, monotonic)
    best_segment = None
    best_score = -1

    for i in range(len(turning_points) - 1):
        idx_start = turning_points[i]
        idx_end = turning_points[i + 1]

        H_seg = H_primary[idx_start:idx_end + 1]
        M_seg = M_primary[idx_start:idx_end + 1]

        # Check if H is monotonically increasing
        is_increasing = np.all(np.diff(H_seg) > -1e-6)

        # Score: prefer segments starting from low H and spanning wide range
        if is_increasing and len(H_seg) > 3:
            H_start = np.abs(H_seg[0])
            H_range = np.max(H_seg) - np.min(H_seg)
            score = H_range / (H_start + 100)  # Prefer low start, wide range

            if score > best_score:
                best_score = score
                best_segment = i

    if best_segment is not None:
        idx_start = turning_points[best_segment]
        idx_end = turning_points[best_segment + 1]

        print(f"\n=== Selected Initial Curve ===")
        print(f"Using segment {best_segment}: indices {idx_start} to {idx_end}")
    else:
        # Fallback: use first segment
        idx_start = turning_points[0]
        idx_end = turning_points[1]
        print(f"\n=== Using First Segment (Fallback) ===")

    # Extract and process initial curve
    H_init = H_primary[idx_start:idx_end + 1]
    M_init = M_primary[idx_start:idx_end + 1]
    B_init = B_primary[idx_start:idx_end + 1]

    # Sort by H to ensure monotonic (in case of minor fluctuations)
    if len(H_init) > 1:
        sort_idx = np.argsort(H_init)
        H_init = H_init[sort_idx]
        M_init = M_init[sort_idx]
        B_init = B_init[sort_idx]

        # Remove duplicate H values
        unique_mask = np.concatenate([[True], np.diff(H_init) > 1e-6])
        H_init = H_init[unique_mask]
        M_init = M_init[unique_mask]
        B_init = B_init[unique_mask]

        print(f"Initial curve points: {len(H_init)}")
        print(f"H range: {H_init.min():.1f} to {H_init.max():.1f} A/m")

        # === Method 1: Total permeability mu_r = B/(mu0*H) ===
        mu_r_total = np.zeros_like(H_init)
        valid_H = np.abs(H_init) > 10  # Avoid very small H

        with np.errstate(divide='ignore', invalid='ignore'):
            mu_r_total[valid_H] = np.abs(B_init[valid_H]) / (mu0 * np.abs(H_init[valid_H]))

        # === Method 2: Differential permeability dB/dH ===
        if len(H_init) > 3:
            # Smooth the curves
            B_smooth = gaussian_filter1d(B_init, sigma=0.5)
            dB_dH = np.gradient(B_smooth, H_init)
            mu_r_diff = dB_dH / mu0

            # Clean unrealistic values
            mu_r_diff = np.clip(mu_r_diff, 1, 1e6)
        else:
            mu_r_diff = mu_r_total.copy()

        # === FIX 2: Improved maximum permeability detection ===
        # Use a more reasonable range that includes the peak near zero field
        H_max = np.max(np.abs(H_init))
        # Only exclude very low fields and very high fields
        valid_range = (np.abs(H_init) > 50) & (np.abs(H_init) < 0.95 * H_max)

        if np.sum(valid_range) > 0:
            mu_max_total = np.max(mu_r_total[valid_range])
            mu_max_diff = np.max(mu_r_diff[valid_range])

            idx_max_total = np.argmax(mu_r_total[valid_range])
            idx_max_diff = np.argmax(mu_r_diff[valid_range])

            # Get actual H values where maximum occurs
            H_values_in_range = H_init[valid_range]
            H_at_max_total = H_values_in_range[idx_max_total]
            H_at_max_diff = H_values_in_range[idx_max_diff]

            print(f"\n=== Permeability Results ===")
            print(f"Method 1 (B/mu0H):  max = {mu_max_total:.1f} at H = {H_at_max_total:.0f} A/m")
            print(f"Method 2 (dB/dH):   max = {mu_max_diff:.1f} at H = {H_at_max_diff:.0f} A/m")
        else:
            # Use all valid points if range filtering too strict
            mu_max_total = np.max(mu_r_total[valid_H]) if np.sum(valid_H) > 0 else 1
            mu_max_diff = np.max(mu_r_diff[valid_H]) if np.sum(valid_H) > 0 else 1
            print(f"\n=== Permeability Results (using all points) ===")
            print(f"Method 1: max = {mu_max_total:.1f}")
            print(f"Method 2: max = {mu_max_diff:.1f}")

    else:
        # Not enough data points
        H_init = H_primary
        M_init = M_primary
        B_init = B_primary
        mu_r_total = np.ones_like(H_init)
        mu_r_diff = np.ones_like(H_init)
        mu_max_total = 1
        mu_max_diff = 1
        print("\n⚠️ Warning: Insufficient data for permeability calculation")

    # Hysteresis loss (area of loop) - FIXED CALCULATION
    # Loss = integral of M dH around the complete loop
    hysteresis_loss = 0

    if len(turning_points) >= 3:
        # Method 1: Direct calculation using complete loop segments
        # Segment 0->1: descending branch (usually Hmax -> -Hmax)
        # Segment 1->2: ascending branch (usually -Hmax -> Hmax)

        idx0, idx1, idx2 = turning_points[0], turning_points[1], turning_points[2]

        # Descending branch
        H_desc = H_primary[idx0:idx1 + 1]
        M_desc = M_primary[idx0:idx1 + 1]

        # Ascending branch
        H_asc = H_primary[idx1:idx2 + 1]
        M_asc = M_primary[idx1:idx2 + 1]

        if len(H_desc) > 2 and len(H_asc) > 2:
            try:
                # Calculate area using the shoelace formula for a closed loop
                # For each branch, integrate M dH, then take the difference

                # Create interpolation functions
                # Note: H_desc goes from high to low, H_asc goes from low to high
                # We need to evaluate both on the same H grid

                # Determine common H range
                H_min = max(H_desc.min(), H_asc.min())
                H_max = min(H_desc.max(), H_asc.max())

                if H_max > H_min:
                    # Create common H grid
                    H_common = np.linspace(H_min, H_max, 200)

                    # Interpolate M values on both branches
                    f_desc = interp1d(H_desc, M_desc, kind='linear', fill_value='extrapolate')
                    f_asc = interp1d(H_asc, M_asc, kind='linear', fill_value='extrapolate')

                    M_desc_interp = f_desc(H_common)
                    M_asc_interp = f_asc(H_common)

                    # Hysteresis loss = area between ascending and descending branches
                    # = integral[(M_asc - M_desc) dH]
                    hysteresis_loss = abs(integrate.trapz(M_asc_interp - M_desc_interp, H_common))

                    print(f"\nHysteresis loss calculation:")
                    print(f"  H range: {H_min:.1f} to {H_max:.1f} A/m")
                    print(f"  Loop area: {hysteresis_loss:.6e} J/m^3")
                else:
                    # Alternative method: use the complete loop path integral
                    # Loss = |∮ M dH| along the closed loop
                    area_desc = integrate.trapz(M_desc, H_desc)
                    area_asc = integrate.trapz(M_asc, H_asc)
                    hysteresis_loss = abs(area_asc - area_desc)

                    print(f"\nHysteresis loss (path integral method):")
                    print(f"  Descending integral: {area_desc:.6e}")
                    print(f"  Ascending integral: {area_asc:.6e}")
                    print(f"  Loop area: {hysteresis_loss:.6e} J/m^3")

            except Exception as e:
                print(f"\nWarning: Hysteresis loss calculation failed: {e}")
                # Fallback: simple polygon area calculation
                try:
                    # Combine both branches into a closed loop
                    H_loop = np.concatenate([H_desc, H_asc[::-1]])
                    M_loop = np.concatenate([M_desc, M_asc[::-1]])

                    # Shoelace formula for polygon area
                    area = 0.5 * abs(np.sum(H_loop[:-1] * M_loop[1:] - H_loop[1:] * M_loop[:-1]))
                    hysteresis_loss = area
                    print(f"\nHysteresis loss (polygon method): {hysteresis_loss:.6e} J/m^3")
                except:
                    pass

    results = {
        'Ms': Ms_real,  # In A/m (FIXED!)
        'Mr': Mr_real,  # In A/m
        'Hc': Hc,
        'mu_r_max_total': mu_max_total,
        'mu_r_max_diff': mu_max_diff,
        'hysteresis_loss': hysteresis_loss,
        'Mr_Ms_ratio': Mr_real / Ms_real if Ms_real > 0 else 0,
        'M_mag_min': np.min(M_mag),
        'M_mag_mean': np.mean(M_mag),
        'Msat': Msat,  # Store Msat for reference
        # For detailed curves
        'H_curve': H_init,
        'M_curve': M_init,
        'B_curve': B_init,
        'mu_r_total': mu_r_total,
        'mu_r_diff': mu_r_diff,
        # Full data for plotting
        'H_full': H_primary,
        'M_full': M_primary,
        'B_full': B_primary
    }

    return results, H_primary, M_primary, B_mag_total, M_mag, E_total


def plot_hysteresis_curves(H, M, B, M_mag, E_total, results):
    """Plot hysteresis loop and permeability analysis

    Note: M is now in A/m (real units), not normalized
    """

    fig = plt.figure(figsize=(16, 12))
    gs = fig.add_gridspec(3, 2, hspace=0.3, wspace=0.3)

    # === Plot 1: M-H hysteresis loop ===
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(H / 1000, M / 1e6, 'b-', linewidth=2, alpha=0.8)  # M in MA/m
    ax1.axhline(y=0, color='k', linestyle='--', alpha=0.3)
    ax1.axvline(x=0, color='k', linestyle='--', alpha=0.3)
    ax1.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax1.set_ylabel('Magnetization M (MA/m)', fontsize=11)
    ax1.set_title('Hysteresis Loop (M-H)', fontsize=13, fontweight='bold')
    ax1.grid(True, alpha=0.3)

    # Mark key points
    zero_H = np.abs(H) < 500
    if np.sum(zero_H) > 0:
        ax1.plot(H[zero_H] / 1000, M[zero_H] / 1e6, 'ro', markersize=6,
                 label=f'Mr={results["Mr"] / 1e6:.3f} MA/m')
    zero_M = np.abs(M) < 0.05 * results['Ms']
    if np.sum(zero_M) > 0:
        ax1.plot(H[zero_M] / 1000, M[zero_M] / 1e6, 'go', markersize=6,
                 label=f'Hc={results["Hc"]:.0f} A/m')
    ax1.legend(fontsize=9)

    # === Plot 2: B-H curve ===
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(H / 1000, results['B_full'] * 1000, 'r-', linewidth=2, alpha=0.8)
    ax2.axhline(y=0, color='k', linestyle='--', alpha=0.3)
    ax2.axvline(x=0, color='k', linestyle='--', alpha=0.3)
    ax2.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax2.set_ylabel('Magnetic Induction B (mT)', fontsize=11)
    ax2.set_title('B-H Curve', fontsize=13, fontweight='bold')
    ax2.grid(True, alpha=0.3)

    # === Plot 3: Total permeability ===
    ax3 = fig.add_subplot(gs[1, 0])
    H_curve = results['H_curve']
    mu_r_total = results['mu_r_total']
    valid_idx = np.abs(H_curve) > 10

    if np.sum(valid_idx) > 0:
        ax3.plot(H_curve[valid_idx] / 1000, mu_r_total[valid_idx],
                 'g-', linewidth=2, alpha=0.8, label='Total permeability')
        ax3.axhline(y=results['mu_r_max_total'], color='r',
                    linestyle='--', linewidth=1.5,
                    label=f'Max = {results["mu_r_max_total"]:.0f}')

    ax3.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax3.set_ylabel('Relative Permeability', fontsize=11)
    ax3.set_title('Total Permeability (B/mu0*H)', fontsize=13, fontweight='bold')
    ax3.set_ylim(bottom=0)
    ax3.grid(True, alpha=0.3)
    ax3.legend(fontsize=9)

    # === Plot 4: Differential permeability ===
    ax4 = fig.add_subplot(gs[1, 1])
    mu_r_diff = results['mu_r_diff']

    if np.sum(valid_idx) > 0:
        ax4.plot(H_curve[valid_idx] / 1000, mu_r_diff[valid_idx],
                 'm-', linewidth=2, alpha=0.8, label='Differential permeability')
        ax4.axhline(y=results['mu_r_max_diff'], color='r',
                    linestyle='--', linewidth=1.5,
                    label=f'Max = {results["mu_r_max_diff"]:.0f}')

    ax4.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax4.set_ylabel('Differential Permeability', fontsize=11)
    ax4.set_title('Differential Permeability (dB/dH)', fontsize=13, fontweight='bold')
    ax4.set_ylim(bottom=0)
    ax4.grid(True, alpha=0.3)
    ax4.legend(fontsize=9)

    # === Plot 5: Energy curve ===
    ax5 = fig.add_subplot(gs[2, 0])
    ax5.plot(H / 1000, E_total * 1e16, 'orange', linewidth=2, alpha=0.8)
    ax5.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax5.set_ylabel('Total Energy E (x10^-16 J)', fontsize=11)
    ax5.set_title('Magnetic Energy vs H', fontsize=13, fontweight='bold')
    ax5.grid(True, alpha=0.3)

    # === Plot 6: |M| magnitude check ===
    ax6 = fig.add_subplot(gs[2, 1])
    ax6.plot(H / 1000, M_mag, 'purple', linewidth=2, alpha=0.8)
    ax6.axhline(y=1.0, color='r', linestyle='--',
                linewidth=1.5, label='Expected Ms=1.0 (normalized)')
    ax6.axhline(y=0.95, color='orange', linestyle=':', linewidth=1)
    ax6.fill_between(H / 1000, 0.95, 1.0, alpha=0.1, color='green')

    problem = M_mag < 0.95
    if np.sum(problem) > 0:
        ax6.scatter(H[problem] / 1000, M_mag[problem], c='red', s=20, alpha=0.5)

    ax6.set_xlabel('Magnetic Field H (kA/m)', fontsize=11)
    ax6.set_ylabel('|M| Magnitude (normalized)', fontsize=11)
    ax6.set_title('Magnetization Magnitude Check', fontsize=13, fontweight='bold')
    ax6.legend(fontsize=9)
    ax6.grid(True, alpha=0.3)

    return fig


def print_results(results):
    """Print magnetic properties"""
    mu0 = 4 * np.pi * 1e-7

    print("\n" + "=" * 75)
    print("              Magnetic Properties Extraction Results")
    print("=" * 75)
    print(f"{'Parameter':<35} {'Value':<25} {'Unit/Note'}")
    print("-" * 75)
    print(f"{'Input Msat (material)':<35} {results['Msat']:.2e} {'A/m'}")
    print(f"{'Saturation Ms (from sim)':<35} {results['Ms']:.2e} {'A/m'}")
    print(f"{'Remanence Mr':<35} {results['Mr']:.2e} {'A/m'}")
    print(f"{'Coercivity Hc':<35} {results['Hc']:.2f} {'A/m'}")
    print(f"{'Coercivity Hc':<35} {results['Hc'] * 4 * np.pi / 1000:.4f} {'Oe'}")
    print(f"{'Max permeability (B/mu0H)':<35} {results['mu_r_max_total']:.0f} {''}")
    print(f"{'Max permeability (dB/dH)':<35} {results['mu_r_max_diff']:.0f} {''}")
    print(f"{'Hysteresis loss':<35} {results['hysteresis_loss']:.6e} {'J/m^3'}")
    print(f"{'Squareness Mr/Ms':<35} {results['Mr_Ms_ratio']:.6f} {''}")
    print("=" * 75)

    print("\n" + "=" * 75)
    print("              Data Quality & Comparison")
    print("=" * 75)
    print(f"{'|M| minimum (normalized)':<35} {results['M_mag_min']:.6f}")
    print(f"{'|M| mean (normalized)':<35} {results['M_mag_mean']:.6f}")

    if results['M_mag_min'] >= 0.95:
        print(">>> GOOD: Magnetization quality excellent")

    print("\n[Typical Fe-Si Properties for Reference]")
    print("  Grain-oriented:    mu_r_max = 30,000-50,000, Mr/Ms = 0.02-0.05")
    print("  Non-oriented:      mu_r_max = 2,000-8,000,   Mr/Ms = 0.05-0.15")
    print(f"  Your simulation:   mu_r_max ~ {results['mu_r_max_diff']:.0f}, "
          f"Mr/Ms = {results['Mr_Ms_ratio']:.3f}")

    print("\n[Hysteresis Loss Information]")
    print(f"  Loop area (energy loss per cycle): {results['hysteresis_loss']:.3e} J/m^3")
    print(f"  Equivalent to: {results['hysteresis_loss'] / 1000:.3e} kJ/m^3")
    if results['hysteresis_loss'] > 0:
        print("  Note: Lower hysteresis loss indicates better soft magnetic properties")


def export_femm_data(results, filename='femm_bh_curve.txt'):
    """Export B-H curve for FEMM"""
    H_curve = results['H_curve']
    B_curve = results['B_curve']

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("# B-H curve for FEMM\n")
        f.write("# H(A/m)\tB(T)\n")
        for h, b in zip(H_curve, B_curve):
            f.write(f"{h:.6e}\t{b:.6e}\n")

    print(f"\n>>> FEMM B-H curve exported: {filename}")


def export_permeability_data(results, filename='permeability_curve.txt'):
    """Export permeability curves"""
    H_curve = results['H_curve']
    mu_r_total = results['mu_r_total']
    mu_r_diff = results['mu_r_diff']

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("# Permeability curves\n")
        f.write("# H(A/m)\tmu_r_total(B/mu0H)\tmu_r_diff(dB/dH)\n")
        for h, mu_t, mu_d in zip(H_curve, mu_r_total, mu_r_diff):
            if abs(h) > 10:
                f.write(f"{h:.6e}\t{mu_t:.6e}\t{mu_d:.6e}\n")

    print(f">>> Permeability curves exported: {filename}")


# ============== Main Program ==============
if __name__ == "__main__":
    filename = "table.txt"
    Msat = 1.56e6  # Fe-3%Si saturation magnetization (A/m), Moses 2012

    print("=" * 75)
    print("     MuMax3 Magnetic Properties Extractor (FIXED VERSION)")
    print("=" * 75)

    data = read_mumax_data(filename)
    print(f"\nLoaded {len(data)} data points from {filename}")

    results, H, M, B, M_mag, E_total = extract_magnetic_properties(data, Msat=Msat)

    print_results(results)

    export_femm_data(results)
    export_permeability_data(results)

    fig = plot_hysteresis_curves(H, M, B, M_mag, E_total, results)
    plt.savefig('magnetic_analysis_complete.png', dpi=300, bbox_inches='tight')
    print(f"\n>>> Plot saved: magnetic_analysis_complete.png")
    plt.show()