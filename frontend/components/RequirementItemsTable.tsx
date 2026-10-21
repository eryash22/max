import type { Asset, Branch, Consumable, DeviceModel, InventoryItem, RequirementItem, RequirementItemInput } from "../types";
import { REQUIREMENT_ITEM_TYPES, REQUIREMENT_ITEM_TYPE_LABELS } from "../types";
import { SearchableSelect } from "./SearchableSelect";

// Terminal asset statuses — matches backend services/asset_lifecycle_service.py's
// _CONDEMNED_VALUES/_RETIRED_VALUES. An asset already in one of these states cannot
// be condemned again, so it's excluded from the condemnation picker (also enforced
// server-side in services/requirement_service.py:_validate_item_reference).
const CONDEMNATION_INELIGIBLE_STATUSES = new Set(["CONDEMNED", "RETIRED"]);

let rowKeySeq = 0;
function nextRowKey() {
  rowKeySeq += 1;
  return `req-item-row-${rowKeySeq}`;
}

export interface ItemRow {
  key: string;
  item_type: string;
  reference_id: string;
  description: string;
  requested_quantity: string;
  unit: string;
  remarks: string;
}

export function makeEmptyRow(): ItemRow {
  return {
    key: nextRowKey(),
    item_type: "DEVICE_MODEL",
    reference_id: "",
    description: "",
    requested_quantity: "1",
    unit: "",
    remarks: "",
  };
}

export function rowsFromItems(items: RequirementItem[]): ItemRow[] {
  if (items.length === 0) return [makeEmptyRow()];
  return items.map((item) => ({
    key: nextRowKey(),
    item_type: item.item_type,
    reference_id: item.device_model_id ?? item.consumable_id ?? item.inventory_item_id ?? item.asset_id ?? "",
    description: item.description ?? "",
    requested_quantity: String(item.requested_quantity),
    unit: item.unit ?? "",
    remarks: item.remarks ?? "",
  }));
}

export function validateItemRows(rows: ItemRow[]): string | null {
  if (rows.length === 0) return "At least one item is required.";
  for (const row of rows) {
    const quantity = Number(row.requested_quantity);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      return "Every item must have a quantity greater than 0.";
    }
    if (row.item_type === "OTHER") {
      if (!row.description.trim()) return "Every OTHER item needs a description.";
    } else if (!row.reference_id) {
      return "Every item must reference a device model, inventory item, or consumable.";
    }
  }
  return null;
}

export function buildItemsPayload(rows: ItemRow[]): RequirementItemInput[] {
  return rows.map((row) => {
    const item: RequirementItemInput = {
      item_type: row.item_type,
      requested_quantity: Number(row.requested_quantity),
      unit: row.unit || null,
      remarks: row.remarks || null,
      description: row.item_type === "OTHER" ? row.description : null,
    };
    if (row.item_type === "DEVICE" || row.item_type === "DEVICE_MODEL") item.device_model_id = row.reference_id;
    if (row.item_type === "CONSUMABLE") item.consumable_id = row.reference_id;
    if (row.item_type === "SPARE_PART") item.inventory_item_id = row.reference_id;
    if (row.item_type === "CONDEMNATION") item.asset_id = row.reference_id;
    return item;
  });
}

export function RequirementItemsTable({
  rows,
  onChange,
  deviceModels,
  deviceModelsLoading = false,
  deviceModelsError = null,
  inventoryItems,
  consumables,
  assets = [],
  branches = [],
  branchId = "",
  disabled = false,
}: {
  rows: ItemRow[];
  onChange: (rows: ItemRow[]) => void;
  deviceModels: DeviceModel[];
  // Correction pass — Requirements > New Requirement > Requested Items > Device no
  // longer goes through a Category -> Model two-step; it's one searchable Device
  // dropdown over the full catalog (see SearchableSelect), so the picker needs to
  // tell "still loading the catalog" and "the catalog failed to load" apart from a
  // genuine empty result — a plain array can't carry that, hence these two optional
  // status props (from useLookup's withStatus option) alongside the array itself.
  deviceModelsLoading?: boolean;
  deviceModelsError?: string | null;
  inventoryItems: InventoryItem[];
  consumables: Consumable[];
  assets?: Asset[];
  branches?: Branch[];
  // The branch the requirement is being raised for — narrows the CONDEMNATION asset
  // picker to that branch's own physical assets only (a condemnation must target an
  // asset in the same branch as the requirement, enforced server-side too — see
  // services/requirement_service.py:_validate_item_reference).
  branchId?: string;
  disabled?: boolean;
}) {
  function updateRow(key: string, patch: Partial<ItemRow>) {
    onChange(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function addRow() {
    onChange([...rows, makeEmptyRow()]);
  }

  function removeRow(key: string) {
    if (rows.length <= 1) return;
    onChange(rows.filter((row) => row.key !== key));
  }

  function deviceOptions() {
    // Search matches the same fields the backend's own device-model list search
    // covers (routers/devices.py list_device_models: name, model_number,
    // manufacturer) — no new/invented fields.
    return deviceModels.map((m) => ({
      id: m.id,
      label: `${m.name}${m.model_number ? ` (${m.model_number})` : ""}`,
      sublabel: m.manufacturer ?? undefined,
    }));
  }

  function referenceOptions(row: ItemRow) {
    const itemType = row.item_type;
    if (itemType === "CONSUMABLE") {
      return consumables.map((c) => {
        const category = c.category ? `${c.category} — ` : "";
        const model = c.compatible_model_number ? ` (${c.compatible_model_number})` : "";
        return { id: c.id, label: `${category}${c.name}${model}`, opening_quantity: c.opening_quantity, branch_id: c.branch_id };
      });
    }
    if (itemType === "SPARE_PART") {
      return inventoryItems.map((i) => ({ id: i.id, label: i.name }));
    }
    if (itemType === "CONDEMNATION") {
      // Shows current status inline (per the condemnation workflow's approval-context
      // requirement) so the picker itself already answers "what state is this asset
      // in?" without a second lookup — same reasoning as consumables showing
      // location + quantity in their own label. Narrowed to the requirement's own
      // branch when one is selected — a condemnation always targets a physical asset
      // in that same branch (backend rejects a mismatch regardless of this filter).
      const branchAssets = (branchId ? assets.filter((a) => a.branch_id === branchId) : assets).filter(
        (a) => !CONDEMNATION_INELIGIBLE_STATUSES.has((a.status ?? "").toUpperCase())
      );
      return branchAssets.map((a) => {
        const branchLabel = branches.find((b) => b.id === a.branch_id)?.name ?? a.branch_id;
        const tag = a.asset_number ?? a.serial_number ?? a.id;
        return { id: a.id, label: `${tag} — ${branchLabel} (currently: ${a.status})` };
      });
    }
    return [];
  }

  return (
    <div>
      <div className="table-wrap" style={{ marginBottom: 12 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Item Category</th>
              <th>Item Reference / Description</th>
              <th>Quantity</th>
              <th>Unit</th>
              <th>Remarks</th>
              {!disabled && <th></th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const isDevice = row.item_type === "DEVICE" || row.item_type === "DEVICE_MODEL";
              const options = isDevice ? [] : referenceOptions(row);
              return (
                <tr key={row.key}>
                  <td>
                    <select
                      value={row.item_type}
                      disabled={disabled}
                      onChange={(e) => updateRow(row.key, { item_type: e.target.value, reference_id: "", description: "" })}
                    >
                      {REQUIREMENT_ITEM_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {REQUIREMENT_ITEM_TYPE_LABELS[t] ?? t.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {row.item_type === "OTHER" ? (
                      <input
                        value={row.description}
                        disabled={disabled}
                        onChange={(e) => updateRow(row.key, { description: e.target.value })}
                        placeholder="Describe the item"
                        required
                      />
                    ) : isDevice ? (
                      <div style={{ minWidth: 220 }}>
                        <SearchableSelect
                          options={deviceOptions()}
                          value={row.reference_id}
                          onChange={(id) => updateRow(row.key, { reference_id: id })}
                          placeholder="Search device..."
                          disabled={disabled}
                          loading={deviceModelsLoading}
                          error={deviceModelsError ? "Failed to load devices — try again" : null}
                          emptyMessage="No devices found"
                        />
                      </div>
                    ) : (
                      <select
                        value={row.reference_id}
                        disabled={disabled}
                        onChange={(e) => updateRow(row.key, { reference_id: e.target.value })}
                        required
                        style={{ width: "100%", minWidth: 200 }}
                      >
                        <option value="" disabled>
                          {options.length === 0 ? "No items available" : "Select item"}
                        </option>
                        {options.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={row.requested_quantity}
                      disabled={disabled}
                      onChange={(e) => updateRow(row.key, { requested_quantity: e.target.value })}
                      required
                      style={{ width: 80 }}
                    />
                  </td>
                  <td>
                    <input
                      value={row.unit}
                      disabled={disabled}
                      onChange={(e) => updateRow(row.key, { unit: e.target.value })}
                      placeholder="e.g. pcs"
                      style={{ width: 90 }}
                    />
                  </td>
                  <td>
                    <input
                      value={row.remarks}
                      disabled={disabled}
                      onChange={(e) => updateRow(row.key, { remarks: e.target.value })}
                      placeholder="Optional"
                    />
                  </td>
                  {!disabled && (
                    <td>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={() => removeRow(row.key)}
                        disabled={rows.length <= 1}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!disabled && (
        <button type="button" className="btn btn--ghost" onClick={addRow} style={{ marginBottom: 16 }}>
          + Add item
        </button>
      )}
    </div>
  );
}
