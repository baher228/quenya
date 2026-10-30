import { useCallback, useState } from "react";
import { img } from "./api";
import type { Item } from "./types";

type ItemImageProps = {
  item: Item;
  alt: string;
  loading?: "eager" | "lazy";
  draggable?: boolean;
};

export function ItemImage({
  item,
  alt,
  loading,
  draggable,
}: ItemImageProps) {
  const source = img(item);
  const [loadedSource, setLoadedSource] = useState<string | null>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const [cachedSource, setCachedSource] = useState<string | null>(null);
  const imageRef = useCallback((image: HTMLImageElement | null) => {
    if (image?.complete && image.naturalWidth > 0) {
      setLoadedSource(source);
      setCachedSource(source);
    }
  }, [source]);
  const loaded = loadedSource === source;
  const failed = failedSource === source;

  return (
    <div className="item-image" aria-busy={!loaded && !failed}>
      {item.image_preview && (
        <img
          className="item-image-preview"
          src={item.image_preview}
          alt=""
          aria-hidden="true"
          draggable={false}
        />
      )}
      {source && (
        <img
          ref={imageRef}
          className={
            "item-image-full" +
            (loaded ? " loaded" : "") +
            (cachedSource === source ? " cached" : "")
          }
          src={source}
          alt={alt}
          loading={loading}
          draggable={draggable}
          onLoad={() => setLoadedSource(source)}
          onError={() => setFailedSource(source)}
        />
      )}
      {!loaded && !failed && <span className="item-image-spinner" aria-hidden="true" />}
    </div>
  );
}
