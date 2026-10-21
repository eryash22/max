"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export interface SearchableSelectOption {
  id: string;
  label: string;
  // Extra searchable/displayed text (e.g. model number, manufacturer) — matched by
  // search but rendered smaller/secondary, same idea as RequirementItemsTable's
  // existing CONDEMNATION picker showing an asset's branch/status alongside its tag.
  sublabel?: string;
}

let idSeq = 0;

/** Accessible type-ahead combobox over an already-loaded option list (client-side
 * filtering — see requirements/new/page.tsx's use of useLookup's fetchAll for why the
 * full list is loaded once up front rather than re-queried per keystroke). Not
 * specific to devices — kept generic so any future picker needing "search a list,
 * pick one" can reuse it instead of growing a second one-off implementation.
 *
 * The option list is rendered through a portal into document.body rather than as a
 * normal absolutely-positioned child: this control lives inside a `.table-wrap`
 * (globals.css sets `overflow-x: auto`, which per the CSS spec forces the other axis
 * to compute as `auto` too), so an in-flow dropdown gets silently clipped by that
 * ancestor's scroll box. Portaling and computing its position from the trigger's own
 * bounding rect avoids that regardless of which scrollable container it's used in. */
export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Search...",
  disabled = false,
  loading = false,
  error = null,
  emptyMessage = "No results found",
}: {
  options: SearchableSelectOption[];
  value: string;
  onChange: (id: string) => void;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  error?: string | null;
  emptyMessage?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const [mounted, setMounted] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useMemo(() => `searchable-select-listbox-${++idSeq}`, []);

  useEffect(() => setMounted(true), []);

  const selected = options.find((o) => o.id === value) ?? null;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || (o.sublabel ?? "").toLowerCase().includes(q)
    );
  }, [options, query]);

  useEffect(() => {
    setHighlighted(0);
  }, [query, open]);

  const updateRect = () => {
    const el = containerRef.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    setRect({ top: box.bottom, left: box.left, width: box.width });
  };

  useLayoutEffect(() => {
    if (!open) return;
    updateRect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      if (containerRef.current?.contains(target)) return;
      const portalEl = document.getElementById(listboxId);
      if (portalEl?.contains(target)) return;
      setOpen(false);
      setQuery("");
    }
    function handleReposition() {
      updateRect();
    }
    document.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("scroll", handleReposition, true);
    window.addEventListener("resize", handleReposition);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("scroll", handleReposition, true);
      window.removeEventListener("resize", handleReposition);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, listboxId]);

  function openList() {
    if (disabled) return;
    setOpen(true);
    setQuery("");
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  function selectOption(option: SearchableSelectOption) {
    onChange(option.id);
    setOpen(false);
    setQuery("");
  }

  function handleKeyDown(event: KeyboardEvent) {
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "Enter") {
        event.preventDefault();
        openList();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((i) => Math.min(i + 1, filtered.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (filtered[highlighted]) selectOption(filtered[highlighted]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setQuery("");
    }
  }

  const listbox = open && rect && (
    <ul
      id={listboxId}
      role="listbox"
      style={{
        position: "fixed",
        zIndex: 1000,
        top: rect.top + 2,
        left: rect.left,
        width: rect.width,
        maxHeight: 260,
        overflowY: "auto",
        background: "var(--surface, #fff)",
        border: "1px solid var(--border, #ccc)",
        borderRadius: 4,
        boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
        padding: 0,
        listStyle: "none",
      }}
    >
      {loading ? (
        <li style={{ padding: "8px 10px", color: "var(--text-muted, #888)" }}>Loading...</li>
      ) : error ? (
        <li style={{ padding: "8px 10px", color: "var(--danger, #c0392b)" }}>{error}</li>
      ) : filtered.length === 0 ? (
        <li style={{ padding: "8px 10px", color: "var(--text-muted, #888)" }}>{emptyMessage}</li>
      ) : (
        filtered.map((option, index) => (
          <li
            key={option.id}
            role="option"
            aria-selected={option.id === value}
            onMouseDown={(e) => {
              e.preventDefault();
              selectOption(option);
            }}
            onMouseEnter={() => setHighlighted(index)}
            style={{
              padding: "6px 10px",
              cursor: "pointer",
              background: index === highlighted ? "var(--surface-selected, rgba(99,102,241,0.12))" : undefined,
              fontWeight: option.id === value ? 600 : 400,
            }}
          >
            <div>{option.label}</div>
            {option.sublabel && (
              <div style={{ fontSize: "0.85em", color: "var(--text-muted, #888)" }}>{option.sublabel}</div>
            )}
          </li>
        ))
      )}
    </ul>
  );

  return (
    <div ref={containerRef} style={{ position: "relative", width: "100%" }}>
      {open ? (
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          style={{ width: "100%" }}
        />
      ) : (
        <button
          type="button"
          onClick={openList}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          style={{
            width: "100%",
            textAlign: "left",
            background: "var(--input-bg, #fff)",
            border: "1px solid var(--border, #ccc)",
            borderRadius: 4,
            padding: "6px 8px",
            cursor: disabled ? "not-allowed" : "pointer",
            color: selected ? "inherit" : "var(--text-muted, #888)",
          }}
        >
          {selected ? selected.label : placeholder}
        </button>
      )}

      {mounted && listbox && createPortal(listbox, document.body)}
    </div>
  );
}
