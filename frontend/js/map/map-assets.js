const ASSET_BASE = "/assets/arena-hero/png";
const ASSET_FILES = { resource: "resource-crystal" };

export function mapAssetUrl(name) {
  return `${ASSET_BASE}/${ASSET_FILES[name] || name}-128.png`;
}

export async function loadMapAssets() {
  const names = ["core", "worker", "vanguard", "ranger", "beacon", "resource", "obstacle"];
  const entries = await Promise.all(names.map(async (name) => {
    const image = new Image();
    image.src = mapAssetUrl(name);
    await image.decode().catch(() => null);
    return [name, image];
  }));
  return Object.fromEntries(entries);
}
