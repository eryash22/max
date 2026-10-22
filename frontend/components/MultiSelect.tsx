"use client";

import { useEffect, useRef, useState } from "react";

export interface MultiSelectOption {
  value: string;
  label: string;
}

/** A toolbar filter control that behaves like the plain `<select>` elements next to
 * it, but lets several values be picked at once (e.g. Open + Work in Progress
 * together) — a native `<select multiple>` needs ctrl/cmd-click, which almost no
 * one discovers, so this is a button that opens a checkbox list instead. */
export function MultiSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: MultiSelectOption[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  }

  const buttonText =
    selected.length === 0
      ? `All ${label.toLowerCase()}`
      : selected.length === 1
        ? options.find((o) => o.value === selected[0])?.label ?? selected[0]
        : `${label} (${selected.length})`;

  return (
    <div className="multi-select" ref={rootRef}>
      <button
        type="button"
        className="multi-select__button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {buttonText}
        <span className="multi-select__caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="multi-select__panel" role="listbox" aria-label={label}>
          {options.map((opt) => (
            <label key={opt.value} className="multi-select__option">
              <input
                type="checkbox"
                checked={selected.includes(opt.value)}
                onChange={() => toggle(opt.value)}
              />
              {opt.label}
            </label>
          ))}
          {selected.length > 0 && (
            <button type="button" className="multi-select__clear" onClick={() => onChange([])}>
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}
