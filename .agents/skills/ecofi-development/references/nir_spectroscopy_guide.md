# AS7263 NIR Spectroscopy & Material Discrimination Guide
> **Sensor Configuration:** SparkFun AS7263 6-Channel NIR (`0x49` I2C) @ 64x Gain, 50mA bulb drive, 140ms integration.

---

## 1. Spectral Channels & Physics of Discrimination

| Channel | Peak Wavelength | Primary Absorption / Reflectance Feature |
| :---: | :---: | :--- |
| **R** | 610 nm | Visible Red / Color absorption |
| **S** | 680 nm | Deep Red / Chlorophyll & dye absorption |
| **T** | 730 nm | Near-IR transition band |
| **U** | 760 nm | Oxygen absorption & polymer overtone |
| **V** | 810 nm | C-H stretch 3rd overtone region |
| **W** | 860 nm | **Primary Benchmark (Cal-W):** High polymer transmission / reflection |

---

## 2. Empirical Calibration Values & Threshold Envelope

Based on real-world testing documented in [`docs/AS7263_NIR_CALIBRATION_RESEARCH.md`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/docs/AS7263_NIR_CALIBRATION_RESEARCH.md):

```
       [ DARK ABSORBERS ]          [ EMPTY AIR ]       [ CLEAR / TINTED PET ]          [ DIFFUSE SCATTERERS ]
        (Colored Glass)              (Baseline)          (Acceptable Bottles)             (Paper / Cardboard)
 0 -------------------------- 22.0 ------------ 28.0 -------------------------- 220 ------------------------> Cal-W (uW/cm2)
        < REJECT >                   < IDLE >                 < ACCEPT >                    < REJECT >
```

- **Empty Air Baseline:** Bounded at **~24.5 $\mu\text{W}/\text{cm}^2$** ($W \approx 22$ counts, Sum $\approx 1000$).
- **Clear PET Bottle Walls:** **$35 - 65\ \mu\text{W}/\text{cm}^2$** (smooth); up to **$\sim 200\ \mu\text{W}/\text{cm}^2$** on ribbed/corrugated walls.
- **BOPP / Cellophane Product Labels:** **$103 - 238\ \mu\text{W}/\text{cm}^2$** due to diffuse Lambertian scattering.
- **Colored Glass Bottles (Beer / Wine):** Strongly absorb NIR (**$8.9 - 17.8\ \mu\text{W}/\text{cm}^2$**, below empty air). Rejection threshold: `Cal-W < 22.0 uW/cm²`.
- **Cardboard / Paper Cups:** Intense diffuse scattering (**$R > 8000$, Raw Sum $> 15,000$, $\text{Cal-W} > 230\ \mu\text{W}/\text{cm}^2$**).

---

## 3. Multimodal Sensor Fusion Decision Matrix

| Sensor Modality | Detected Phenomenon | Target Rejection / Acceptance |
| :--- | :--- | :--- |
| **LJ12A3 Inductive** | Eddy currents in conductive metal | Rejects aluminum soda cans & glass bottles with metal crown caps. |
| **AS7263 NIR (`Cal-W`)** | Molecular transmission & diffuse reflection | Accepts valid PET bottles ($28 \le W \le 220$). Rejects colored glass ($<22$) and paper cups ($>220$). |
| **Dual IR (E18-D80NK)** | Optical beam transit interruption | Verifies physical entrance into the chute and confirms gravitational drop into the storage bin. |
| **Weight Sensor (HX711)** | Load mass cutoff (optional) | Guards against excessively heavy foreign objects or filled liquid containers. |
