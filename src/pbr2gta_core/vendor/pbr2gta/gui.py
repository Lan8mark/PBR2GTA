from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .converter import (
    ConversionResult,
    Spec2GtaResult,
    convert_material,
    convert_spec2gta,
)


class ConverterWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PBR2GTA Lite")
        self.minsize(760, 540)
        self.geometry("820x590")

        self.pipeline_var = tk.StringVar(value="pbr2gta")
        self.base_var = tk.StringVar()
        self.metal_var = tk.StringVar()
        self.rough_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.stem_var = tk.StringVar()
        self.specular_mode_var = tk.StringVar(value="weapon")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Select three maps.")
        self.result_var = tk.StringVar(value="")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_result: ConversionResult | Spec2GtaResult | None = None
        self.input_labels: list[ttk.Label] = []

        self._build()
        self._sync_pipeline()
        self.after(100, self._drain_events)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Pipeline").grid(row=0, column=0, sticky="w", pady=7)
        self.pipeline_combo = ttk.Combobox(
            frame,
            textvariable=self.pipeline_var,
            values=("pbr2gta", "spec2gta"),
            state="readonly",
            width=16,
        )
        self.pipeline_combo.grid(row=0, column=1, sticky="w", padx=10, pady=7)
        self.pipeline_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_pipeline())

        rows = [
            ("Base Color", self.base_var, self._pick_base),
            ("Metallic", self.metal_var, self._pick_metal),
            ("Roughness", self.rough_var, self._pick_rough),
            ("Output folder", self.output_var, self._pick_output),
        ]
        for offset, (label, variable, command) in enumerate(rows, start=1):
            label_widget = ttk.Label(frame, text=label)
            label_widget.grid(row=offset, column=0, sticky="w", pady=7)
            self.input_labels.append(label_widget)
            ttk.Entry(frame, textvariable=variable).grid(
                row=offset, column=1, sticky="ew", padx=10, pady=7
            )
            ttk.Button(frame, text="Browse", command=command).grid(
                row=offset, column=2, pady=7
            )

        ttk.Label(frame, text="Asset name").grid(row=5, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.stem_var).grid(
            row=5, column=1, sticky="ew", padx=10, pady=7
        )

        ttk.Label(frame, text="Output mode").grid(row=6, column=0, sticky="w", pady=7)
        self.output_mode_combo = ttk.Combobox(
            frame,
            textvariable=self.specular_mode_var,
            values=("weapon", "default"),
            state="readonly",
            width=16,
        )
        self.output_mode_combo.grid(row=6, column=1, sticky="w", padx=10, pady=7)

        ttk.Checkbutton(
            frame,
            text="Overwrite existing output files",
            variable=self.overwrite_var,
        ).grid(row=7, column=1, sticky="w", padx=10, pady=(3, 12))

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 5))
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=9, column=0, columnspan=3, sticky="w"
        )

        controls = ttk.Frame(frame)
        controls.grid(row=10, column=0, columnspan=3, sticky="ew", pady=14)
        self.convert_button = ttk.Button(controls, text="Convert", command=self._convert)
        self.convert_button.pack(side="left")
        self.open_button = ttk.Button(
            controls,
            text="Open folder",
            command=self._open_output,
            state="disabled",
        )
        self.open_button.pack(side="left", padx=8)
        self.copy_button = ttk.Button(
            controls,
            text="Copy parameters",
            command=self._copy_parameters,
            state="disabled",
        )
        self.copy_button.pack(side="left")

        result_box = ttk.LabelFrame(frame, text="Result", padding=12)
        result_box.grid(row=11, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(11, weight=1)
        ttk.Label(
            result_box,
            textvariable=self.result_var,
            justify="left",
            anchor="nw",
            wraplength=730,
        ).pack(fill="both", expand=True)

    def _sync_pipeline(self) -> None:
        pipeline = self.pipeline_var.get()
        if pipeline == "spec2gta":
            labels = ("Diffuse", "Specular", "Gloss", "Output folder")
            modes = ("weapon", "default", "both")
            status = "Select Diffuse, Specular, Gloss."
        else:
            labels = ("Base Color", "Metallic", "Roughness", "Output folder")
            modes = ("weapon", "default")
            status = "Select Base Color, Metallic, Roughness."
            if self.specular_mode_var.get() == "both":
                self.specular_mode_var.set("weapon")

        for label_widget, text in zip(self.input_labels, labels):
            label_widget.configure(text=text)
        self.output_mode_combo.configure(values=modes)
        if self.specular_mode_var.get() not in modes:
            self.specular_mode_var.set(modes[0])
        self.status_var.set(status)

    def _pick_base(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
        if path:
            self.base_var.set(path)
            if not self.stem_var.get():
                self.stem_var.set(Path(path).stem)

    def _pick_metal(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
        if path:
            self.metal_var.set(path)

    def _pick_rough(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
        if path:
            self.rough_var.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _convert(self) -> None:
        required = [
            self.base_var.get(),
            self.metal_var.get(),
            self.rough_var.get(),
            self.output_var.get(),
        ]
        if not all(required):
            messagebox.showerror(
                "PBR2GTA",
                "Select three input maps and an output folder.",
            )
            return

        self.convert_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.progress["value"] = 0
        self.result_var.set("")

        def worker() -> None:
            try:
                if self.pipeline_var.get() == "spec2gta":
                    result = convert_spec2gta(
                        diffuse_path=self.base_var.get(),
                        specular_path=self.metal_var.get(),
                        gloss_path=self.rough_var.get(),
                        output_directory=self.output_var.get(),
                        stem=self.stem_var.get() or None,
                        output_mode=self.specular_mode_var.get(),
                        overwrite=self.overwrite_var.get(),
                        progress_callback=lambda phase, value: self.events.put(
                            ("progress", (phase, value))
                        ),
                    )
                else:
                    result = convert_material(
                        base_color_path=self.base_var.get(),
                        metallic_path=self.metal_var.get(),
                        roughness_path=self.rough_var.get(),
                        output_directory=self.output_var.get(),
                        stem=self.stem_var.get() or None,
                        specular_mode=self.specular_mode_var.get(),
                        overwrite=self.overwrite_var.get(),
                        progress_callback=lambda phase, value: self.events.put(
                            ("progress", (phase, value))
                        ),
                    )
                self.events.put(("success", result))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _format_result(self, result: ConversionResult | Spec2GtaResult) -> str:
        params = result.parameters
        if params.get("Pipeline") == "spec2gta":
            lines = [
                "Pipeline: spec2gta",
                f"Output mode: {params['OutputMode']}",
                f"Specular remap: {params['SpecularRemapMin']}..{params['SpecularRemapMax']}",
                f"Gloss weapon power: {params['GlossWeaponPower']:.1f}",
                f"Gloss default power: {params['GlossDefaultPower']:.1f}",
            ]
            if result.weapon_specular_path:
                lines.append(f"Weapon specular: {Path(result.weapon_specular_path).name}")
            if result.default_specular_path:
                lines.append(f"Default normal_spec: {Path(result.default_specular_path).name}")
            lines.extend(["", result.output_directory])
            return "\n".join(lines)

        return "\n".join(
            [
                f"SpecFresnel: {params['SpecFresnel']:.6f}",
                f"SpecFalloffMult: {params['SpecFalloffMult']:.6f}",
                f"SpecIntMult: {params['SpecIntMult']:.6f}",
                f"Specular mode: {params['SpecularExportMode']}",
                f"Spec2Factor: {params['Spec2Factor']:.6f}",
                f"Spec2ColorInt: {params['Spec2ColorInt']:.6f}",
                f"Spec2Color: {params['Spec2ColorHex']}  "
                f"({params['Spec2ColorPackedHex']})",
                f"Spec2 enabled: {params['Spec2Enabled']}",
                "",
                result.output_directory,
            ]
        )

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    phase, value = payload  # type: ignore[misc]
                    self.progress["value"] = float(value) * 100
                    self.status_var.set(str(phase))
                elif kind == "success":
                    self.last_result = payload  # type: ignore[assignment]
                    self.result_var.set(self._format_result(self.last_result))
                    self.convert_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.copy_button.configure(state="normal")
                elif kind == "error":
                    self.convert_button.configure(state="normal")
                    self.status_var.set("Error")
                    messagebox.showerror("PBR2GTA", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _open_output(self) -> None:
        if not self.last_result:
            return
        path = self.last_result.output_directory
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _copy_parameters(self) -> None:
        if not self.last_result:
            return
        p = self.last_result.parameters
        if p.get("Pipeline") == "spec2gta":
            text = json.dumps(p, ensure_ascii=False, indent=2)
        else:
            text = "\n".join(
                [
                    f"SpecFresnel={p['SpecFresnel']:.6f}",
                    f"SpecFalloffMult={p['SpecFalloffMult']:.6f}",
                    f"SpecIntMult={p['SpecIntMult']:.6f}",
                    f"SpecularMode={p['SpecularExportMode']}",
                    f"Spec2Factor={p['Spec2Factor']:.6f}",
                    f"Spec2ColorInt={p['Spec2ColorInt']:.6f}",
                    f"Spec2Color={p['Spec2ColorPackedHex']}",
                ]
            )
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Parameters copied.")


def main() -> None:
    ConverterWindow().mainloop()


if __name__ == "__main__":
    main()
