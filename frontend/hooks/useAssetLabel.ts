"use client";

import { useLookup } from "./useLookup";
import type { Asset, Branch, DeviceModel } from "../types";

/** Resolves an asset to a readable label ("Device name — Branch — ref") instead of
 * the raw UUID these pages used to fall back to whenever `asset_number` was unset —
 * true for almost every bulk-imported asset in this install, so that fallback was
 * showing on nearly every page that lists/links an asset by id. Self-contained
 * (fetches its own assets/device-models/branches) so it drops in anywhere without
 * requiring the page to already have those lookups loaded. */
export function useAssetLabel() {
  const assets = useLookup<Asset>("/api/v1/assets");
  const models = useLookup<DeviceModel>("/api/v1/device-models");
  const branches = useLookup<Branch>("/api/v1/branches");

  const assetById = new Map(assets.map((a) => [a.id, a]));
  const modelById = new Map(models.map((m) => [m.id, m]));
  const branchById = new Map(branches.map((b) => [b.id, b]));

  function labelForAsset(a: Asset | undefined | null): string {
    if (!a) return "Unknown device";
    const model = modelById.get(a.device_model_id);
    const branch = branchById.get(a.branch_id);
    const ref = a.asset_number ?? a.serial_number ?? null;
    return [model?.name ?? "Unknown device", branch?.name, ref].filter(Boolean).join(" — ");
  }

  function labelForAssetId(id: string | null | undefined): string {
    if (!id) return "-";
    return labelForAsset(assetById.get(id));
  }

  return { assets, models, branches, assetById, modelById, branchById, labelForAsset, labelForAssetId };
}
