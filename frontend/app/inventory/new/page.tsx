"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ProtectedShell } from "../../../components/ProtectedShell";
import { ErrorState } from "../../../components/ErrorState";
import { useAuth } from "../../../hooks/useAuth";
import { useLookup } from "../../../hooks/useLookup";
import { api, ApiError } from "../../../lib/api";
import type { Branch, Consumable, DeviceCategory, DeviceCreateResult, DeviceModel, Vendor } from "../../../types";
import { CONSUMABLE_CATEGORIES, DEVICE_CATEGORIES } from "../../../types";

// Batch D, Part 10 — "New Inventory Item" creation flow redesign. ITEM TYPE and
// DEPARTMENT/CATEGORY come first; everything after them branches on that choice.
// DEVICE wires to the existing composite POST /api/v1/devices endpoint (Part D of
// the earlier restructure pass — reuses/creates a DeviceModel and always creates the
// physical Asset in one transaction; that's why Asset Number/Serial Number appear
// here — this screen creates an asset-level record, not just a catalog DeviceModel).
// CONSUMABLE wires to POST /api/v1/consumables (the `Consumable` model — branch_id
// NULL is the central/hub pool). No new backend endpoints were added: both already
// support everything this form needs.

type ItemType = "DEVICE" | "CONSUMABLE";

export default function NewInventoryItemPage() {
  const router = useRouter();
  const { hasPermission } = useAuth();
  const canDevice = hasPermission("device.write") && hasPermission("asset.write");
  const canConsumable = hasPermission("inventory.write");

  const [itemType, setItemType] = useState<ItemType>(canDevice ? "DEVICE" : "CONSUMABLE");
  const [category, setCategory] = useState("");
  const [useExisting, setUseExisting] = useState(true);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [selectedConsumableCode, setSelectedConsumableCode] = useState("");
  const [vendorId, setVendorId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const deviceCategories = useLookup<DeviceCategory>("/api/v1/device-categories");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  // status_filter=ACTIVE — a deactivated branch/vendor must not be selectable when
  // creating a new inventory item (see backend/app/routers/hospitals.py, vendors.py).
  const branches = useLookup<Branch>("/api/v1/branches?status_filter=ACTIVE");
  const vendors = useLookup<Vendor>("/api/v1/vendors?status_filter=ACTIVE");
  // fetchAll so the "existing consumable" picker (filtered by category client-side)
  // isn't silently missing rows past the default 200-row lookup page.
  const consumables = useLookup<Consumable>("/api/v1/consumables", { fetchAll: true });

  const matchingModels = models.filter((m) => !category || m.category === category);
  // One entry per distinct catalog item (item_code+name), not one per branch stock
  // row — picking "existing item" here is about the catalog identity, the branch
  // picker below decides which stock row gets created/topped up.
  const matchingConsumables = Array.from(
    new Map(
      consumables
        .filter((c) => !category || c.category === category)
        .map((c) => [c.item_code, c])
    ).values()
  );

  if (!canDevice && !canConsumable) {
    return (
      <ProtectedShell>
        <ErrorState message="You do not have permission to create inventory items." />
      </ProtectedShell>
    );
  }

  const selectedExistingModel = models.find((m) => m.id === selectedModelId);
  const selectedExistingConsumable = consumables.find((c) => c.item_code === selectedConsumableCode);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const formData = new FormData(event.currentTarget);

    try {
      if (itemType === "DEVICE") {
        const payload: Record<string, unknown> = {
          branch_id: formData.get("branch_id"),
          serial_number: formData.get("serial_number") || null,
          asset_number: formData.get("asset_number") || null,
          status: "Pending",
        };
        if (useExisting) {
          if (!selectedModelId) throw new ApiError(422, "Select an existing device model, or switch to Create new item.");
          payload.device_model_id = selectedModelId;
        } else {
          const matchedCategory = deviceCategories.find((c) => c.name === category);
          payload.model_name = formData.get("model_name");
          payload.manufacturer = formData.get("manufacturer") || null;
          payload.model_number = formData.get("model_number") || null;
          payload.category = category;
          payload.category_id = matchedCategory ? matchedCategory.id : null;
        }
        const result = await api.post<DeviceCreateResult>("/api/v1/devices", payload);

        if (vendorId && hasPermission("vendor.write")) {
          // Best-effort: link the chosen vendor to the (new-or-reused) device model
          // via the existing vendor<->device-model endpoint. A failure here (e.g. the
          // link already exists) must not roll back or block the device that was
          // just created — it's already saved and the user is navigating to it.
          try {
            await api.post(`/api/v1/vendors/${vendorId}/device-models`, {
              device_model_id: result.device_model.id,
              rank: 0,
            });
          } catch {
            // Non-fatal — vendor linkage can be added later from the vendor or asset page.
          }
        }
        router.push(`/assets/${result.asset.id}`);
      } else {
        if (useExisting && !selectedConsumableCode) {
          throw new ApiError(422, "Select an existing consumable item, or switch to Create new item.");
        }
        const branchId = (formData.get("branch_id") as string) || null;
        const payload: Record<string, unknown> = {
          item_code: useExisting ? selectedConsumableCode : formData.get("item_code"),
          name: useExisting ? selectedExistingConsumable?.name : formData.get("name"),
          branch_id: branchId,
          category: category || null,
          opening_quantity: formData.get("quantity") ? Number(formData.get("quantity")) : null,
          vendor_id: formData.get("vendor_id") || null,
        };
        const item = await api.post<Consumable>("/api/v1/consumables", payload);
        router.push(`/consumables/${item.id}`);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create inventory item.");
      setSubmitting(false);
    }
  }

  return (
    <ProtectedShell>
      <div className="page-header">
        <div>
          <h1>New inventory item</h1>
          <p>Add a physical device (asset) or consumable stock item — the fields below adapt to your choice.</p>
        </div>
      </div>

      <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 760 }}>
        <div className="form-grid">
          <div className="form-field">
            <label>Item type*</label>
            <select
              value={itemType}
              onChange={(e) => {
                setItemType(e.target.value as ItemType);
                setCategory("");
                setSelectedModelId("");
                setSelectedConsumableCode("");
              }}
            >
              {canDevice && <option value="DEVICE">Device</option>}
              {canConsumable && <option value="CONSUMABLE">Consumable</option>}
            </select>
          </div>
          <div className="form-field">
            <label>Department / category*</label>
            <select
              required
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setSelectedModelId("");
                setSelectedConsumableCode("");
              }}
            >
              <option value="" disabled>
                Select department / category
              </option>
              {(itemType === "DEVICE" ? DEVICE_CATEGORIES : CONSUMABLE_CATEGORIES).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-field form-field--checkbox" style={{ margin: "12px 0" }}>
          <label>
            <input
              type="radio"
              name="mode"
              checked={useExisting}
              onChange={() => setUseExisting(true)}
            />{" "}
            Use existing item
          </label>
          <label style={{ marginLeft: 16 }}>
            <input type="radio" name="mode" checked={!useExisting} onChange={() => setUseExisting(false)} /> Create
            new item
          </label>
        </div>

        {itemType === "DEVICE" ? (
          <>
            {useExisting ? (
              <div className="form-grid">
                <div className="form-field">
                  <label>Existing device model*</label>
                  <select
                    required
                    value={selectedModelId}
                    onChange={(e) => setSelectedModelId(e.target.value)}
                  >
                    <option value="" disabled>
                      {category ? "Select device model" : "Select a department/category first"}
                    </option>
                    {matchingModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name} {m.manufacturer ? `(${m.manufacturer})` : ""}
                      </option>
                    ))}
                  </select>
                  {category && matchingModels.length === 0 && (
                    <span className="field-hint">No existing device models in this category — switch to &quot;Create new item&quot;.</span>
                  )}
                </div>
                {selectedExistingModel && (
                  <>
                    <div className="form-field">
                      <label>Manufacturer / Brand</label>
                      <input value={selectedExistingModel.manufacturer ?? "-"} disabled />
                    </div>
                    <div className="form-field">
                      <label>Model number</label>
                      <input value={selectedExistingModel.model_number ?? "-"} disabled />
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="form-grid">
                <div className="form-field">
                  <label>Device name*</label>
                  <input name="model_name" required />
                </div>
                <div className="form-field">
                  <label>Manufacturer / Brand</label>
                  <input name="manufacturer" />
                </div>
                <div className="form-field">
                  <label>Model number</label>
                  <input name="model_number" />
                </div>
              </div>
            )}

            <h3>Physical asset</h3>
            <div className="form-grid">
              <div className="form-field">
                <label>Asset number (optional)</label>
                <input name="asset_number" placeholder="Can be added later" />
              </div>
              <div className="form-field">
                <label>Serial number</label>
                <input name="serial_number" />
              </div>
              <div className="form-field">
                <label>Branch / Warehouse*</label>
                <select name="branch_id" required defaultValue="">
                  <option value="" disabled>
                    Select branch
                  </option>
                  {branches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.is_central_warehouse ? `${b.name} (Central warehouse)` : b.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label>Vendor</label>
                {hasPermission("vendor.write") ? (
                  <select value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
                    <option value="">Not set</option>
                    {vendors.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="field-hint">You don&apos;t have permission to link a vendor here — an admin can add it from the vendor page.</span>
                )}
              </div>
            </div>
            <div className="card" style={{ background: "var(--color-surface-elevated)", marginTop: 12 }}>
              <strong>AMC / CMC / Warranty</strong>
              <p style={{ marginBottom: 0 }}>
                Coverage isn&apos;t set here — it&apos;s derived live from the Contract module after saving, from the asset&apos;s detail page.
              </p>
            </div>
          </>
        ) : (
          <>
            {useExisting && (
              <div className="form-grid">
                <div className="form-field">
                  <label>Existing consumable item*</label>
                  <select
                    required
                    value={selectedConsumableCode}
                    onChange={(e) => setSelectedConsumableCode(e.target.value)}
                  >
                    <option value="" disabled>
                      {category ? "Select consumable item" : "Select a department/category first"}
                    </option>
                    {matchingConsumables.map((c) => (
                      <option key={c.item_code} value={c.item_code}>
                        {c.name} ({c.item_code})
                      </option>
                    ))}
                  </select>
                  {category && matchingConsumables.length === 0 && (
                    <span className="field-hint">No existing consumable items in this category — switch to &quot;Create new item&quot;.</span>
                  )}
                </div>
              </div>
            )}
            <div className="form-grid">
              <div className="form-field">
                <label>Consumable name*</label>
                {useExisting ? (
                  <input value={selectedExistingConsumable?.name ?? ""} disabled />
                ) : (
                  <input name="name" required />
                )}
              </div>
              {!useExisting && (
                <div className="form-field">
                  <label>Item code*</label>
                  <input name="item_code" required placeholder="e.g. CON-001" />
                </div>
              )}
              <div className="form-field">
                <label>Quantity</label>
                <input name="quantity" type="number" min={0} placeholder="Opening quantity" />
              </div>
              <div className="form-field">
                <label>Supplier / vendor</label>
                {hasPermission("vendor.write") ? (
                  <select name="vendor_id" defaultValue="">
                    <option value="">Not set</option>
                    {vendors.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="field-hint">
                    You don&apos;t have permission to link a vendor here — an admin can add it from the vendor page.
                  </span>
                )}
              </div>
              <div className="form-field">
                <label>Branch (leave blank for central / hub stock)</label>
                <select name="branch_id" defaultValue="">
                  <option value="">Central / hub stock</option>
                  {branches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </>
        )}

        {error && (
          <div className="field-error" style={{ marginTop: 16 }}>
            {error}
          </div>
        )}
        <button className="btn btn--primary" type="submit" disabled={submitting} style={{ marginTop: 16 }}>
          {submitting ? "Saving..." : "Create item"}
        </button>
      </form>
    </ProtectedShell>
  );
}
