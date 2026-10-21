import { TimetableData, TimetableItem } from '../types/api';

const DATABASE_NAME = 'classwire';
const STORE_NAME = 'timetables';
const SEARCH_STORE_NAME = 'search-results';
const SOURCE_STORE_NAME = 'timetable-sources';
const DATABASE_VERSION = 3;
const LEGACY_PREFIX = 'classwire:v2:last-timetable:';
const memoryCache = new Map<string, TimetableData>();
const searchMemoryCache = new Map<string, TimetableData>();
const sourceMemoryCache = new Map<string, CachedTimetableSource>();
let databasePromise: Promise<IDBDatabase | null> | null = null;

export interface CachedTimetableSource {
  items: TimetableItem[];
  snapshot_version: number;
  parser_version?: number;
  refreshed_at_by_day: Record<string, string>;
  source_received_at_by_day?: Record<string, string>;
}

const hasVerifiedEmailDate = (
  refreshed: Record<string, string>,
  received: Record<string, string> | undefined,
  day: string,
) => {
  const refreshDate = Date.parse(refreshed[day] || '');
  const emailDate = Date.parse(received?.[day] || '');
  return Number.isFinite(refreshDate) && Number.isFinite(emailDate)
    && Math.abs(refreshDate - emailDate) <= 60_000;
};

export const verifiedRefreshDates = (data: TimetableData | null): Record<string, string> => {
  const refreshed = data?.source_cache?.refreshed_at_by_day || {};
  const received = data?.source_cache?.source_received_at_by_day;
  return Object.fromEntries(Object.entries(refreshed).filter(([day]) =>
    hasVerifiedEmailDate(refreshed, received, day)));
};

const normalizeKey = (email?: string) => (email || 'anonymous').trim().toLowerCase();

const openDatabase = (): Promise<IDBDatabase | null> => {
  if (!('indexedDB' in window)) return Promise.resolve(null);
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve) => {
    const request = window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
      if (!request.result.objectStoreNames.contains(SEARCH_STORE_NAME)) {
        request.result.createObjectStore(SEARCH_STORE_NAME);
      }
      if (!request.result.objectStoreNames.contains(SOURCE_STORE_NAME)) {
        request.result.createObjectStore(SOURCE_STORE_NAME);
      }
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => {
        request.result.close();
        databasePromise = null;
      };
      resolve(request.result);
    };
    request.onerror = () => {
      databasePromise = null;
      resolve(null);
    };
  });
  return databasePromise;
};

const transact = async <T>(
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
  storeName = STORE_NAME,
) => {
  const database = await openDatabase();
  if (!database) return undefined;
  return new Promise<T | undefined>((resolve) => {
    const transaction = database.transaction(storeName, mode);
    const request = operation(transaction.objectStore(storeName));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(undefined);
    transaction.onerror = () => resolve(undefined);
    transaction.onabort = () => resolve(undefined);
  });
};

const validTimetable = (value: unknown): value is TimetableData =>
  Boolean(value && typeof value === 'object' && Array.isArray((value as TimetableData).items));

const normalizeQuery = (query: string) => query.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
const searchKey = (email: string | undefined, query: string) => `${normalizeKey(email)}\n${normalizeQuery(query)}`;

export const isSearchCacheFresh = (data: TimetableData): boolean =>
  data.source_cache?.snapshot_version === 1;

const validSource = (value: unknown): value is CachedTimetableSource => Boolean(
  value && typeof value === 'object'
  && Array.isArray((value as CachedTimetableSource).items)
  && (value as CachedTimetableSource).snapshot_version === 1,
);

export const isUsableWeeklySource = (source: CachedTimetableSource): boolean =>
  source.snapshot_version === 1;

export const readTimetableSourceCache = async (
  email?: string,
): Promise<CachedTimetableSource | null> => {
  const key = normalizeKey(email);
  const memoryResult = sourceMemoryCache.get(key);
  if (memoryResult) return memoryResult;
  const result = await transact<CachedTimetableSource>(
    'readonly',
    (store) => store.get(key),
    SOURCE_STORE_NAME,
  );
  if (!validSource(result)) return null;
  sourceMemoryCache.set(key, result);
  return result;
};

export const findCachedTimetableOwner = async (): Promise<string | null> => {
  const database = await openDatabase();
  if (!database) return null;

  for (const storeName of [SOURCE_STORE_NAME, STORE_NAME]) {
    const keys = await transact<IDBValidKey[]>(
      'readonly',
      (store) => store.getAllKeys(),
      storeName,
    );
    const email = keys
      ?.map((key) => String(key).trim().toLowerCase())
      .find((key) => key !== 'anonymous' && key.includes('@'));
    if (email) return email;
  }

  return null;
};

export const writeTimetableSourceCache = async (
  email: string | undefined,
  data: TimetableData,
): Promise<void> => {
  const metadata = data.source_cache;
  if (metadata?.snapshot_version !== 1 || !Array.isArray(metadata.source_items)) return;
  const source: CachedTimetableSource = {
    items: metadata.source_items,
    snapshot_version: metadata.snapshot_version,
    parser_version: metadata.parser_version,
    refreshed_at_by_day: metadata.refreshed_at_by_day || {},
    source_received_at_by_day: metadata.source_received_at_by_day || {},
  };
  const key = normalizeKey(email);
  sourceMemoryCache.set(key, source);
  await transact<IDBValidKey>('readwrite', (store) => store.put(source, key), SOURCE_STORE_NAME);
};

export const readSearchResultCache = async (
  email: string | undefined,
  query: string,
): Promise<TimetableData | null> => {
  const key = searchKey(email, query);
  const memoryResult = searchMemoryCache.get(key);
  if (memoryResult && isSearchCacheFresh(memoryResult)) return memoryResult;
  const result = await transact<TimetableData>(
    'readonly',
    (store) => store.get(key),
    SEARCH_STORE_NAME,
  );
  if (validTimetable(result) && isSearchCacheFresh(result)) {
    searchMemoryCache.set(key, result);
    return result;
  }
  return null;
};

export const clearSearchResultCache = async (email?: string): Promise<void> => {
  const key = normalizeKey(email);
  for (const cachedKey of searchMemoryCache.keys()) {
    if (cachedKey.startsWith(`${key}\n`)) searchMemoryCache.delete(cachedKey);
  }
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction(SEARCH_STORE_NAME, 'readwrite');
    const request = transaction.objectStore(SEARCH_STORE_NAME).openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (String(cursor.key).startsWith(`${key}\n`)) cursor.delete();
      cursor.continue();
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
    transaction.onabort = () => resolve();
  });
};

export const readTimetableCache = async (email?: string): Promise<TimetableData | null> => {
  const key = normalizeKey(email);
  const inMemory = memoryCache.get(key);
  if (inMemory) return inMemory;
  const fromDatabase = await transact<TimetableData>('readonly', (store) => store.get(key));
  if (validTimetable(fromDatabase)) {
    memoryCache.set(key, fromDatabase);
    return fromDatabase;
  }

  // One-time migration from the old synchronous cache.
  try {
    const legacyKey = `${LEGACY_PREFIX}${key}`;
    const raw = window.localStorage.getItem(legacyKey);
    const parsed = raw ? JSON.parse(raw) : null;
    if (validTimetable(parsed)) {
      await writeTimetableCache(email, parsed);
      if ('indexedDB' in window) window.localStorage.removeItem(legacyKey);
      return parsed;
    }
  } catch {
    // A corrupt or unavailable legacy cache should never block startup.
  }
  return null;
};

export const writeTimetableCache = async (email: string | undefined, data: TimetableData): Promise<void> => {
  const key = normalizeKey(email);
  memoryCache.set(key, data);
  const query = data.search?.query?.trim();
  if (query) searchMemoryCache.set(searchKey(email, query), data);
  await writeTimetableSourceCache(email, data);
  const persistedData = data.source_cache?.source_items
    ? {
      ...data,
      source_cache: { ...data.source_cache, source_items: undefined },
    }
    : data;
  const persistedKey = await transact<IDBValidKey>('readwrite', (store) => store.put(persistedData, key));
  if (persistedKey === undefined) {
    try {
      window.localStorage.setItem(`${LEGACY_PREFIX}${key}`, JSON.stringify(persistedData));
    } catch {
      // Keep the in-memory cache when browser persistence is unavailable.
    }
    return;
  }
  try {
    window.localStorage.removeItem(`${LEGACY_PREFIX}${key}`);
  } catch {
    // Ignore cleanup failures.
  }
  if (query) {
    await transact<IDBValidKey>(
      'readwrite',
      (store) => store.put(persistedData, searchKey(email, query)),
      SEARCH_STORE_NAME,
    );
  }
};

export const deleteTimetableCache = async (
  email?: string,
  includeSearchResults = true,
): Promise<void> => {
  const key = normalizeKey(email);
  memoryCache.delete(key);
  if (includeSearchResults) {
    sourceMemoryCache.delete(key);
    for (const cachedKey of searchMemoryCache.keys()) {
      if (cachedKey.startsWith(`${key}\n`)) searchMemoryCache.delete(cachedKey);
    }
  }
  await transact('readwrite', (store) => store.delete(key));
  const database = await openDatabase();
  if (database && includeSearchResults) {
    await transact('readwrite', (store) => store.delete(key), SOURCE_STORE_NAME);
  }
  if (database && includeSearchResults) {
    await new Promise<void>((resolve) => {
      const transaction = database.transaction(SEARCH_STORE_NAME, 'readwrite');
      const request = transaction.objectStore(SEARCH_STORE_NAME).openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if (String(cursor.key).startsWith(`${key}\n`)) cursor.delete();
        cursor.continue();
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => resolve();
      transaction.onabort = () => resolve();
    });
  }
  try {
    window.localStorage.removeItem(`${LEGACY_PREFIX}${key}`);
  } catch {
    // Ignore unavailable storage.
  }
};
