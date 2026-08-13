const ASSET_BASE = "/assets/arena-hero/png";

export async function loadMapAssets() {
  const names = ["core", "worker", "vanguard", "ranger", "beacon", "resource", "obstacle"];
  const entries = await Promise.all(names.map(async (name) => {
    const image = new Image();
    image.src = `${ASSET_BASE}/${name}-128.png`;
    await image.decode().catch(() => null);
    return [name, image];
  }));
  return Object.fromEntries(entries);
}
