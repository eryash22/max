"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Asset, Contract, DeviceModel, DeviceModelVendor, Page, Vendor } from "../types";

/** Archived device models, fetched separately from `useLookup`'s active-only default
 * since `GET /device-models` only supports a binary `is_active` filter with no "all"
 * option, and `useLookup` always appends its own `?page=...` query string (which
 * would collide with an `?is_active=false` already on the path).
 * Used purely for display/name-resolution — never for a "New asset"/"New device"
 * picker (those must only ever offer active models).
 *
 * Extracted from assets/page.tsx (Batch C, Part 3) so the inventory unified view
 * (Batch D, Part 4) can resolve the same archived-model names without duplicating
 * this fetch. */
export function useArchivedDeviceModels() {
  const [items, setItems] = useState<DeviceModel[]>([]);
  useEffect(() => {
    let cancelled = false;
    api
      .get<Page<DeviceModel>>("/api/v1/device-models?is_active=false&page=1&page_size=200")
      .then((result) => {
        if (!cancelled) setItems(result.items);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return items;
}

/** Resolves each asset's primary vendor by joining `device_model_id` through
 * `DeviceModelVendor` (rank 0 = primary), reusing the existing per-vendor
 * `/vendors/{id}/device-models` endpoint (no bulk reverse-lookup endpoint exists on
 * the backend, and adding one is out of scope for a frontend-only pass). Vendor
 * contact fields already arrive pre-masked by the backend for VENDOR_READ_LIMITED
 * callers (Part 5) — this hook never re-implements that check, and never fetches a
 * full vendor record through any other path that would bypass it. */
export function useAssetVendorMap(vendors: Vendor[]) {
  const [map, setMap] = useState<Map<string, Vendor>>(new Map());
  useEffect(() => {
    if (vendors.length === 0) return;
    let cancelled = false;
    Promise.all(
      vendors.map((v) =>
        api
          .get<DeviceModelVendor[]>(`/api/v1/vendors/${v.id}/device-models`)
          .then((links) => links.map((l) => ({ ...l, vendor: v })))
          .catch(() => [])
      )
    ).then((results) => {
      if (cancelled) return;
      const byModel = new Map<string, { rank: number; vendor: Vendor }>();
      for (const links of results) {
        for (const link of links) {
          const existing = byModel.get(link.device_model_id);
          if (!existing || link.rank < existing.rank) {
            byModel.set(link.device_model_id, { rank: link.rank, vendor: link.vendor });
          }
        }
      }
      setMap(new Map(Array.from(byModel.entries()).map(([modelId, v]) => [modelId, v.vendor])));
    });
    return () => {
      cancelled = true;
    };
  }, [vendors]);
  return map;
}

/** AMC/CMC/Warranty coverage label for one asset, derived live from contracts —
 * never stored on the asset itself (see Asset model docstring). Shared by
 * assets/page.tsx, the unified inventory DEVICES tab, and the Service Visit form so
 * all three render the exact same "Coverage" value instead of independently-drifting
 * implementations.
 *
 * Mirrors the authoritative backend resolution in
 * backend/app/services/asset_lifecycle_service.py:get_incident_coverage — strict
 * priority AMC -> CMC -> Warranty, and gated on the computed `expiry_status`
 * (date-aware) rather than the contract's own `status` field, which can lag behind
 * an actual date-based expiry. Whichever of AMC/CMC/Warranty is currently ACTIVE
 * wins; nothing concatenates multiple simultaneously-active contract types. */
export function coverageForAsset(asset: Asset, contracts: Contract[]): { label: string; tone: string } {
  const forAsset = contracts.filter((c) => c.asset_ids.includes(asset.id));
  for (const type of ["AMC", "CMC", "WARRANTY"] as const) {
    const match = forAsset.find((c) => c.contract_type === type && c.expiry_status === "ACTIVE");
    if (match) return { label: match.contract_type, tone: "success" };
  }
  const expiringSoon = forAsset.find((c) => c.expiry_status === "EXPIRING_SOON");
  if (expiringSoon) return { label: `${expiringSoon.contract_type} (expiring soon)`, tone: "warning" };
  return { label: "Not covered", tone: "warning" };
}
