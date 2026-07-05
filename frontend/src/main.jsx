import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BedDouble,
  Building2,
  Flag,
  Heart,
  Home,
  Info,
  LandPlot,
  LogOut,
  MapPin,
  Pencil,
  Phone,
  Plus,
  Ruler,
  Scale,
  Search,
  Loader2,
  Save,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Target,
  TrendingDown,
  X,
} from "lucide-react";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const FILTERS = [
  ["all", "Toutes"],
  ["new", "Nouvelles"],
  ["favorite", "Shortlist"],
  ["call", "A appeler"],
  ["rejected", "Rejetées"],
];

function formatPrice(value) {
  if (!value) return "Prix NC";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
}

function pricePerM2(listing) {
  if (!listing.price_eur || !listing.living_area_m2) return "?";
  return `${formatPrice(Math.round(listing.price_eur / listing.living_area_m2))} / m²`;
}

function formatSignedAmount(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const formatted = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
    signDisplay: "always",
  }).format(value);
  return formatted;
}

function formatShortDate(isoString) {
  try {
    return new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

function scoreTier(score) {
  if (score === null || score === undefined) return "score-mid";
  if (score >= 70) return "score-good";
  if (score >= 40) return "score-mid";
  return "score-low";
}

function renderListItem(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    return item.label || item.text || item.reason || JSON.stringify(item);
  }
  return String(item);
}

function PriceSparkline({ points }) {
  const values = points.map((point) => point.price_eur).filter((value) => typeof value === "number" && Number.isFinite(value));
  if (values.length < 2) return null;
  const width = 280;
  const height = 56;
  const padding = 6;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = (width - padding * 2) / (values.length - 1);
  const coords = values.map((value, index) => {
    const x = padding + index * step;
    const y = padding + (height - padding * 2) * (1 - (value - min) / range);
    return [x, y];
  });
  const path = coords.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const isDrop = values[values.length - 1] < values[0];
  return (
    <svg className="price-sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={path} fill="none" stroke={isDrop ? "var(--color-good)" : "var(--color-ink-faint)"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {coords.map(([x, y], index) => (
        <circle key={index} cx={x} cy={y} r="2.5" fill={isDrop ? "var(--color-good)" : "var(--color-ink-faint)"} />
      ))}
    </svg>
  );
}

const EMPTY_STATE_COPY = {
  all: {
    title: "Aucune annonce pour le moment",
    body: "Ajoute une ville à suivre puis lance un scan pour commencer à recevoir des annonces.",
  },
  new: {
    title: "Pas de nouvelle annonce",
    body: "Lance un scan pour vérifier si de nouveaux biens sont apparus sur les portails suivis.",
  },
  favorite: {
    title: "Aucune annonce en shortlist",
    body: "Marque tes coups de cœur depuis une fiche annonce pour les retrouver ici.",
  },
  call: {
    title: "Rien à appeler pour l'instant",
    body: "Les annonces marquées « A appeler » apparaîtront dans cet onglet.",
  },
  rejected: {
    title: "Aucune annonce rejetée",
    body: "Les biens que tu écartes seront listés ici pour garder l'historique.",
  },
};

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("maisonScoutToken") || "");
  const [user, setUser] = useState(null);
  const [listings, setListings] = useState([]);
  const [runs, setRuns] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [naturalProfiles, setNaturalProfiles] = useState([]);
  const [naturalPromptDraft, setNaturalPromptDraft] = useState("");
  const [naturalNameDraft, setNaturalNameDraft] = useState("");
  const [selectedNaturalProfile, setSelectedNaturalProfile] = useState(null);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ email: "", password: "", display_name: "", invite_code: "" });
  const [newCity, setNewCity] = useState("");
  const [cityCriteria, setCityCriteria] = useState({
    max_price_eur: "",
    min_living_area_m2: "",
    min_land_area_m2: "",
    min_bedrooms: "",
  });
  const [standardFilters, setStandardFilters] = useState({
    max_price_eur: "",
    min_living_area_m2: "",
    min_land_area_m2: "",
    min_bedrooms: "",
    sort: "score",
    price_dropped_only: false,
  });
  const [selectedListing, setSelectedListing] = useState(null);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState([]);
  const [comparisonError, setComparisonError] = useState("");
  const [showComparison, setShowComparison] = useState(false);
  const [priceHistory, setPriceHistory] = useState([]);
  const [priceHistoryLoading, setPriceHistoryLoading] = useState(false);

  function authHeaders() {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadListings() {
    if (!token) return;
    const [meResponse, listingsResponse, runsResponse, profilesResponse, comparisonResponse, naturalProfilesResponse] =
      await Promise.all([
        fetch(`${API_URL}/api/me`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/listings`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/crawl-runs`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/search-profiles`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/comparison`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/natural-search-profiles`, { headers: authHeaders() }),
      ]);
    if (meResponse.status === 401) {
      logout();
      return;
    }
    setUser(await meResponse.json());
    setListings(await listingsResponse.json());
    setRuns(await runsResponse.json());
    setProfiles(await profilesResponse.json());
    setComparison(await comparisonResponse.json());
    setNaturalProfiles(naturalProfilesResponse.ok ? await naturalProfilesResponse.json() : []);
  }

  async function submitAuth(event) {
    event.preventDefault();
    setError("");
    const response = await fetch(`${API_URL}/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(authForm),
    });
    if (!response.ok) {
      setError(authMode === "login" ? "Connexion impossible" : "Inscription impossible");
      return;
    }
    const data = await response.json();
    localStorage.setItem("maisonScoutToken", data.token);
    setToken(data.token);
    setUser(data.user);
  }

  function logout() {
    localStorage.removeItem("maisonScoutToken");
    setToken("");
    setUser(null);
    setListings([]);
    setRuns([]);
    setProfiles([]);
    setComparison([]);
    setShowComparison(false);
    setNaturalProfiles([]);
    setSelectedNaturalProfile(null);
  }

  async function runAllCrawlers() {
    setLoading(true);
    await fetch(`${API_URL}/api/crawl/all`, { method: "POST", headers: authHeaders() });
    await loadListings();
    setLoading(false);
  }

  async function setListingStatus(id, nextStatus, note = undefined) {
    const body = {};
    if (nextStatus !== undefined) body.status = nextStatus;
    if (note !== undefined) body.note = note;
    await fetch(`${API_URL}/api/listings/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    await loadListings();
  }

  async function saveNote(id, note) {
    await setListingStatus(id, undefined, note);
  }

  const comparisonIds = useMemo(() => new Set(comparison.map((listing) => listing.id)), [comparison]);

  async function addToComparison(id) {
    setComparisonError("");
    const response = await fetch(`${API_URL}/api/comparison/${id}`, { method: "POST", headers: authHeaders() });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      setComparisonError(detail?.detail || "Impossible d'ajouter au comparatif");
      return;
    }
    setComparison(await response.json());
  }

  async function removeFromComparison(id) {
    const response = await fetch(`${API_URL}/api/comparison/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
      setComparison(await response.json());
    }
  }

  function toggleComparison(id) {
    if (comparisonIds.has(id)) {
      removeFromComparison(id);
    } else {
      addToComparison(id);
    }
  }

  async function addCity(event) {
    event.preventDefault();
    if (!newCity.trim()) return;
    await fetch(`${API_URL}/api/search-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        city: newCity.trim(),
        max_price_eur: numberOrNull(cityCriteria.max_price_eur),
        min_living_area_m2: numberOrNull(cityCriteria.min_living_area_m2),
        min_land_area_m2: numberOrNull(cityCriteria.min_land_area_m2),
        min_bedrooms: numberOrNull(cityCriteria.min_bedrooms),
      }),
    });
    setNewCity("");
    setCityCriteria({ max_price_eur: "", min_living_area_m2: "", min_land_area_m2: "", min_bedrooms: "" });
    await loadListings();
  }

  async function deleteProfile(profileId) {
    await fetch(`${API_URL}/api/search-profiles/${profileId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadListings();
  }

  async function saveProfile(profile) {
    await fetch(`${API_URL}/api/search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: profile.name,
        city: profile.city,
        source: profile.source,
        max_price_eur: numberOrNull(profile.max_price_eur),
        min_living_area_m2: numberOrNull(profile.min_living_area_m2),
        min_land_area_m2: numberOrNull(profile.min_land_area_m2),
        min_bedrooms: numberOrNull(profile.min_bedrooms),
      }),
    });
    setSelectedProfile(null);
    await loadListings();
  }

  async function createNaturalProfile(event) {
    event.preventDefault();
    if (!naturalPromptDraft.trim()) return;
    await fetch(`${API_URL}/api/natural-search-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: naturalNameDraft.trim() || undefined,
        raw_prompt: naturalPromptDraft.trim(),
      }),
    });
    setNaturalPromptDraft("");
    setNaturalNameDraft("");
    await loadListings();
  }

  async function saveNaturalProfile(profile) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: profile.name,
        raw_prompt: profile.raw_prompt,
        is_active: profile.is_active,
      }),
    });
    setSelectedNaturalProfile(null);
    await loadListings();
  }

  async function toggleNaturalProfileActive(profile) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ is_active: !profile.is_active }),
    });
    await loadListings();
  }

  async function deleteNaturalProfile(profileId) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profileId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadListings();
  }

  function openListing(listing) {
    setSelectedListing(listing);
    setNoteDraft(listing.note || "");
  }

  useEffect(() => {
    loadListings();
  }, [token]);

  useEffect(() => {
    if (!selectedListing || !token) {
      setPriceHistory([]);
      return;
    }
    let cancelled = false;
    setPriceHistoryLoading(true);
    setPriceHistory([]);
    fetch(`${API_URL}/api/listings/${selectedListing.id}/price-history`, { headers: authHeaders() })
      .then((response) => (response.ok ? response.json() : []))
      .then((data) => {
        if (!cancelled) setPriceHistory(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setPriceHistory([]);
      })
      .finally(() => {
        if (!cancelled) setPriceHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedListing?.id, token]);

  const filtered = useMemo(() => {
    const matchesNumber = (value, filterValue, mode) => {
      if (!filterValue) return true;
      // Une donnée manquante ne doit pas masquer l'annonce : on préfère
      // laisser passer plutôt que cacher à tort un bien mal extrait.
      if (value === null || value === undefined) return true;
      const parsed = Number(filterValue);
      if (!Number.isFinite(parsed)) return true;
      return mode === "max" ? value <= parsed : value >= parsed;
    };
    const visible = listings
      .filter((listing) => status === "all" || listing.status === status)
      .filter((listing) => matchesNumber(listing.price_eur, standardFilters.max_price_eur, "max"))
      .filter((listing) => matchesNumber(listing.living_area_m2, standardFilters.min_living_area_m2, "min"))
      .filter((listing) => matchesNumber(listing.land_area_m2, standardFilters.min_land_area_m2, "min"))
      .filter((listing) => matchesNumber(listing.bedrooms, standardFilters.min_bedrooms, "min"))
      .filter((listing) => !standardFilters.price_dropped_only || listing.price_dropped === true);
    return [...visible].sort((a, b) => {
      if (standardFilters.sort === "price") return (a.price_eur ?? Number.MAX_SAFE_INTEGER) - (b.price_eur ?? Number.MAX_SAFE_INTEGER);
      if (standardFilters.sort === "surface") return (b.living_area_m2 ?? 0) - (a.living_area_m2 ?? 0);
      if (standardFilters.sort === "updated") return 0;
      if (standardFilters.sort === "match") {
        if (a.match_score === null || a.match_score === undefined) return 1;
        if (b.match_score === null || b.match_score === undefined) return -1;
        return b.match_score - a.match_score;
      }
      return (b.score ?? 0) - (a.score ?? 0);
    });
  }, [listings, status, standardFilters]);

  const activeListing = selectedListing ? listings.find((listing) => listing.id === selectedListing.id) || selectedListing : null;

  if (!token) {
    return (
      <main className="auth-layout">
        <section className="auth-panel">
          <span className="auth-brand">
            <Home size={16} />
            Maison Scout
          </span>
          <h1>{authMode === "login" ? "Content de te revoir" : "Rejoindre le groupe"}</h1>
          <p>Connecte-toi pour suivre tes villes, trier les annonces et lancer les scans.</p>
          <div className="auth-tabs">
            <button type="button" className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")}>
              Connexion
            </button>
            <button type="button" className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")}>
              Inscription
            </button>
          </div>
          <form onSubmit={submitAuth}>
            {authMode === "register" && (
              <>
                <input
                  placeholder="Nom affiché"
                  value={authForm.display_name}
                  onChange={(event) => setAuthForm({ ...authForm, display_name: event.target.value })}
                />
                <input
                  placeholder="Code invitation"
                  value={authForm.invite_code}
                  onChange={(event) => setAuthForm({ ...authForm, invite_code: event.target.value })}
                />
              </>
            )}
            <input
              placeholder="Email"
              type="email"
              value={authForm.email}
              onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
            />
            <input
              placeholder="Mot de passe"
              type="password"
              value={authForm.password}
              onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
            />
            {error && <p className="error">{error}</p>}
            <button className="primary" type="submit">
              {authMode === "login" ? "Se connecter" : "Créer le compte"}
            </button>
          </form>
          <button className="link-button" onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
            {authMode === "login" ? "Pas encore de compte ? Créer un compte" : "J'ai déjà un compte"}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main>
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-brand-mark">
            <Home size={22} />
          </span>
          <div>
            <h1>Maison Scout</h1>
            <p>{user?.display_name || "Compte"} · annonces centralisées par ville suivie.</p>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="primary" onClick={runAllCrawlers} disabled={loading}>
            {loading ? <Loader2 size={18} className="spin" /> : <Search size={18} />}
            {loading ? "Scan en cours..." : "Scanner"}
          </button>
          <button
            className="ghost compare-trigger"
            onClick={() => setShowComparison(true)}
            disabled={comparison.length === 0}
          >
            <Scale size={18} />
            Comparer
            {comparison.length > 0 && <span className="compare-count">{comparison.length}</span>}
          </button>
          <button className="icon-button" title="Déconnexion" onClick={logout}>
            <LogOut size={18} />
          </button>
        </div>
      </header>
      {comparisonError && <p className="error compare-error">{comparisonError}</p>}

      <section className="profiles">
        <div className="profile-list">
          {profiles.map((profile) => (
            <span className="profile-chip" key={profile.id}>
              <MapPin size={14} className="profile-chip-icon" />
              {profile.city}
              <button title="Réglages" onClick={() => setSelectedProfile(profile)}>
                <Settings size={14} />
              </button>
              <button title="Supprimer" onClick={() => deleteProfile(profile.id)}>
                <X size={14} />
              </button>
            </span>
          ))}
        </div>
        <form className="add-city-form" onSubmit={addCity}>
          <input placeholder="Ajouter une ville" value={newCity} onChange={(event) => setNewCity(event.target.value)} />
          <input
            placeholder="Budget max"
            inputMode="numeric"
            value={cityCriteria.max_price_eur}
            onChange={(event) => setCityCriteria({ ...cityCriteria, max_price_eur: event.target.value })}
          />
          <input
            placeholder="Surf. min"
            inputMode="numeric"
            value={cityCriteria.min_living_area_m2}
            onChange={(event) => setCityCriteria({ ...cityCriteria, min_living_area_m2: event.target.value })}
          />
          <input
            placeholder="Terrain min"
            inputMode="numeric"
            value={cityCriteria.min_land_area_m2}
            onChange={(event) => setCityCriteria({ ...cityCriteria, min_land_area_m2: event.target.value })}
          />
          <input
            placeholder="Ch. min"
            inputMode="numeric"
            value={cityCriteria.min_bedrooms}
            onChange={(event) => setCityCriteria({ ...cityCriteria, min_bedrooms: event.target.value })}
          />
          <button title="Ajouter" type="submit">
            <Plus size={18} />
          </button>
        </form>
      </section>

      <section className="natural-profiles">
        <div className="natural-profiles-header">
          <Sparkles size={17} />
          <span>Recherches en langage naturel</span>
        </div>
        {naturalProfiles.length > 0 && (
          <div className="natural-profile-list">
            {naturalProfiles.map((profile) => {
              const pendingAnalysis = !profile.parsed_model && (!profile.criteria_json || Object.keys(profile.criteria_json).length === 0);
              return (
                <div className={`natural-profile-card${profile.is_active ? "" : " is-inactive"}`} key={profile.id}>
                  <div className="natural-profile-card-top">
                    <p className="natural-profile-name">{profile.name || "Recherche sans nom"}</p>
                    <div className="natural-profile-card-actions">
                      <button title="Éditer" onClick={() => setSelectedNaturalProfile(profile)}>
                        <Pencil size={14} />
                      </button>
                      <button title="Supprimer" onClick={() => deleteNaturalProfile(profile.id)}>
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                  <p className="natural-profile-prompt">{profile.raw_prompt}</p>
                  <div className="natural-profile-card-footer">
                    <button
                      type="button"
                      className={`natural-profile-toggle${profile.is_active ? " is-active" : ""}`}
                      onClick={() => toggleNaturalProfileActive(profile)}
                    >
                      {profile.is_active ? "Active" : "Désactivée"}
                    </button>
                    {pendingAnalysis && <span className="natural-profile-pending">Analyse en attente</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <form className="natural-profile-form" onSubmit={createNaturalProfile}>
          <input
            placeholder="Nom (optionnel)"
            value={naturalNameDraft}
            onChange={(event) => setNaturalNameDraft(event.target.value)}
          />
          <textarea
            placeholder="Décris ce que tu cherches, ex : 4 chambres minimum, piscine, clim, accès de plain-pied au jardin"
            value={naturalPromptDraft}
            onChange={(event) => setNaturalPromptDraft(event.target.value)}
          />
          <button className="primary" type="submit">
            <Plus size={18} />
            Ajouter cette recherche
          </button>
        </form>
      </section>

      <section className="filters">
        {FILTERS.map(([value, label]) => (
          <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>
            {label}
          </button>
        ))}
      </section>

      <section className="standard-filters" aria-label="Affiner l'affichage">
        <div className="standard-filters-title">
          <SlidersHorizontal size={17} />
          <span>Affiner l'affichage</span>
          <span className="standard-filters-hint">filtre temporaire, non enregistré</span>
        </div>
        <input
          placeholder="Budget max"
          inputMode="numeric"
          value={standardFilters.max_price_eur}
          onChange={(event) => setStandardFilters({ ...standardFilters, max_price_eur: event.target.value })}
        />
        <input
          placeholder="Surface min"
          inputMode="numeric"
          value={standardFilters.min_living_area_m2}
          onChange={(event) => setStandardFilters({ ...standardFilters, min_living_area_m2: event.target.value })}
        />
        <input
          placeholder="Terrain min"
          inputMode="numeric"
          value={standardFilters.min_land_area_m2}
          onChange={(event) => setStandardFilters({ ...standardFilters, min_land_area_m2: event.target.value })}
        />
        <input
          placeholder="Chambres min"
          inputMode="numeric"
          value={standardFilters.min_bedrooms}
          onChange={(event) => setStandardFilters({ ...standardFilters, min_bedrooms: event.target.value })}
        />
        <select
          value={standardFilters.sort}
          onChange={(event) => setStandardFilters({ ...standardFilters, sort: event.target.value })}
        >
          <option value="score">Score</option>
          <option value="match">Pertinence (IA)</option>
          <option value="price">Prix croissant</option>
          <option value="surface">Surface décroissante</option>
          <option value="updated">Plus récentes</option>
        </select>
        <label className="standard-filters-checkbox">
          <input
            type="checkbox"
            checked={standardFilters.price_dropped_only}
            onChange={(event) => setStandardFilters({ ...standardFilters, price_dropped_only: event.target.checked })}
          />
          <TrendingDown size={14} />
          Baisse de prix uniquement
        </label>
        <button
          type="button"
          className="ghost compact"
          onClick={() =>
            setStandardFilters({
              max_price_eur: "",
              min_living_area_m2: "",
              min_land_area_m2: "",
              min_bedrooms: "",
              sort: "score",
              price_dropped_only: false,
            })
          }
        >
          Réinitialiser
        </button>
      </section>

      <section className="summary">
        <div>
          <strong>{filtered.length}</strong>
          <span>Affichées</span>
        </div>
        <div>
          <strong>{listings.length}</strong>
          <span>Compatibles profils</span>
        </div>
        <div>
          <strong>{runs[0]?.found_count ?? 0}</strong>
          <span>Trouvées au dernier scan</span>
        </div>
      </section>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state-icon">
            <Search size={24} />
          </span>
          <h3>{(EMPTY_STATE_COPY[status] || EMPTY_STATE_COPY.all).title}</h3>
          <p>{(EMPTY_STATE_COPY[status] || EMPTY_STATE_COPY.all).body}</p>
          {listings.length === 0 && (
            <button className="primary" onClick={runAllCrawlers} disabled={loading}>
              {loading ? <Loader2 size={18} className="spin" /> : <Search size={18} />}
              {loading ? "Scan en cours..." : "Lancer un scan"}
            </button>
          )}
        </div>
      ) : (
        <section className="grid">
          {filtered.map((listing) => (
            <article className="card" key={listing.id}>
              <div className="photo" onClick={() => openListing(listing)}>
                {listing.photos[0] ? <img src={listing.photos[0].url} alt={listing.title} /> : <Home size={44} />}
                {listing.sources[0]?.source && <span className="source-flag">{listing.sources[0].source}</span>}
                <span className={`score-badge ${scoreTier(listing.score)}`}>{listing.score ?? "-"} / 100</span>
                {listing.match_score !== null && listing.match_score !== undefined && (
                  <span className="match-badge" title="Pertinence par rapport à ta recherche IA">
                    <Target size={12} />
                    {listing.match_score}
                  </span>
                )}
                {listing.price_dropped && (
                  <span className="price-drop-badge" title="Le prix de cette annonce a baissé">
                    <TrendingDown size={12} />
                    Baisse de prix
                    {formatSignedAmount(listing.price_change_abs) && listing.price_change_abs < 0 && (
                      <span className="price-drop-amount">{formatSignedAmount(listing.price_change_abs)}</span>
                    )}
                  </span>
                )}
              </div>
              <div className="content">
                <h2 onClick={() => openListing(listing)}>{listing.title}</h2>
                <p className="location">
                  <MapPin size={14} />
                  {listing.city} {listing.postal_code || ""}
                </p>
                <p className="price">{formatPrice(listing.price_eur)}</p>
                {Array.isArray(listing.auto_flags) && listing.auto_flags.length > 0 && (
                  <div className="auto-flags-row">
                    {listing.auto_flags.slice(0, 3).map((flag, index) => (
                      <span
                        key={`${flag.code || flag.label}-${index}`}
                        className={`auto-flag-chip ${flag.severity === "warn" ? "is-warn" : "is-info"}`}
                        title={flag.label}
                      >
                        {flag.severity === "warn" ? <AlertTriangle size={11} /> : <Info size={11} />}
                        {flag.label}
                      </span>
                    ))}
                    {listing.auto_flags.length > 3 && (
                      <span className="auto-flag-chip is-more">+{listing.auto_flags.length - 3}</span>
                    )}
                  </div>
                )}
                <p className="meta">
                  <span className="meta-item">
                    <Ruler size={14} />
                    {listing.living_area_m2 || "?"} m² hab.
                  </span>
                  <span className="meta-item">
                    <LandPlot size={14} />
                    {listing.land_area_m2 || "?"} m² terrain
                  </span>
                  <span className="meta-item">
                    <BedDouble size={14} />
                    {listing.bedrooms || "?"} ch.
                  </span>
                </p>
                <p className="description">{listing.description}</p>
                <div className="card-footer">
                  {listing.note && <p className="note-preview">{listing.note}</p>}
                  <div className="badges-row">
                    {listing.sources[0]?.source && <span className="source-badge">{listing.sources[0].source}</span>}
                    <a className="source" href={listing.sources[0]?.url} target="_blank" rel="noreferrer">
                      Voir l'annonce source
                    </a>
                  </div>
                  <div className="actions">
                    <button
                      className={`action-favorite${listing.status === "favorite" ? " is-active" : ""}`}
                      title="Shortlist"
                      onClick={() => setListingStatus(listing.id, "favorite")}
                    >
                      <Heart size={18} />
                    </button>
                    <button
                      className={`action-call${listing.status === "call" ? " is-active" : ""}`}
                      title="A appeler"
                      onClick={() => setListingStatus(listing.id, "call")}
                    >
                      <Phone size={18} />
                    </button>
                    <button
                      className={`action-reject${listing.status === "rejected" ? " is-active" : ""}`}
                      title="Rejeter"
                      onClick={() => setListingStatus(listing.id, "rejected")}
                    >
                      <X size={18} />
                    </button>
                    <button
                      className={`action-compare${comparisonIds.has(listing.id) ? " is-active" : ""}`}
                      title={comparisonIds.has(listing.id) ? "Retirer du comparatif" : "Ajouter au comparatif"}
                      onClick={() => toggleComparison(listing.id)}
                    >
                      <Scale size={18} />
                    </button>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {activeListing && (
        <div className="modal-backdrop" onClick={() => setSelectedListing(null)}>
          <section className="modal" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" onClick={() => setSelectedListing(null)}>
              <X size={18} />
            </button>
            <div className="detail-photos">
              {(activeListing.photos.length ? activeListing.photos : [{ url: "" }]).slice(0, 4).map((photo, index) => (
                <div className="detail-photo" key={`${photo.url}-${index}`}>
                  {photo.url ? <img src={photo.url} alt="" /> : <Home size={36} />}
                </div>
              ))}
            </div>
            <h2>{activeListing.title}</h2>
            <p className="location">
              <MapPin size={16} />
              {activeListing.city} {activeListing.postal_code || ""}
            </p>
            <p className="price">
              {formatPrice(activeListing.price_eur)}
              {activeListing.price_dropped && (
                <span className="price-drop-badge price-drop-badge-inline" title="Le prix de cette annonce a baissé">
                  <TrendingDown size={12} />
                  Baisse de prix
                  {formatSignedAmount(activeListing.price_change_abs) && activeListing.price_change_abs < 0 && (
                    <span className="price-drop-amount">{formatSignedAmount(activeListing.price_change_abs)}</span>
                  )}
                </span>
              )}
            </p>
            <p className="meta">
              <span className="meta-item">
                <Ruler size={14} />
                {activeListing.living_area_m2 || "?"} m² hab.
              </span>
              <span className="meta-item">
                <LandPlot size={14} />
                {activeListing.land_area_m2 || "?"} m² terrain
              </span>
              <span className="meta-item">
                <Building2 size={14} />
                {activeListing.rooms || "?"} pièces
              </span>
              <span className="meta-item">
                <BedDouble size={14} />
                {activeListing.bedrooms || "?"} ch.
              </span>
            </p>
            <p className="modal-description">{activeListing.description}</p>

            {!priceHistoryLoading && priceHistory.length >= 2 && (
              <div className="price-history-block">
                <h3>
                  <TrendingDown size={14} />
                  Évolution du prix
                </h3>
                <PriceSparkline points={priceHistory} />
                <ul className="price-history-list">
                  {priceHistory.map((point, index) => (
                    <li key={`${point.observed_at}-${index}`}>
                      <span className="price-history-date">{formatShortDate(point.observed_at)}</span>
                      <span className="price-history-value">{formatPrice(point.price_eur)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {Array.isArray(activeListing.auto_flags) && activeListing.auto_flags.length > 0 && (
              <div className="auto-flags-block">
                <h3>
                  <Flag size={14} />
                  Signaux automatiques
                  <span className="auto-flags-block-hint">détection instantanée</span>
                </h3>
                <ul>
                  {activeListing.auto_flags.map((flag, index) => (
                    <li key={`${flag.code || flag.label}-${index}`} className={flag.severity === "warn" ? "is-warn" : "is-info"}>
                      {flag.severity === "warn" ? <AlertTriangle size={13} /> : <Info size={13} />}
                      {flag.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {activeListing.ai_summary && (
              <div className="ai-summary">
                <h3>
                  <Sparkles size={14} />
                  Résumé IA
                </h3>
                <p>{activeListing.ai_summary}</p>
              </div>
            )}

            {activeListing.red_flags && activeListing.red_flags.length > 0 && (
              <div className="ai-red-flags">
                <h3>
                  <AlertTriangle size={14} />
                  Points d'attention
                </h3>
                <ul>
                  {activeListing.red_flags.map((flag, index) => (
                    <li key={`flag-${index}`}>{renderListItem(flag)}</li>
                  ))}
                </ul>
              </div>
            )}

            {activeListing.match_score !== null && activeListing.match_score !== undefined && (
              <div className="ai-match">
                <h3>
                  <Target size={14} />
                  Correspondance avec ta recherche
                  {activeListing.active_profile_name && (
                    <span className="ai-match-profile">{activeListing.active_profile_name}</span>
                  )}
                </h3>
                <div className="ai-match-score">
                  <span className={`score-badge ${scoreTier(activeListing.match_score)}`}>
                    {activeListing.match_score} / 100
                  </span>
                </div>
                {activeListing.match_reasons && activeListing.match_reasons.length > 0 && (
                  <div className="ai-match-group ai-match-good">
                    <p className="ai-match-group-title">Points forts</p>
                    <ul>
                      {activeListing.match_reasons.map((item, index) => (
                        <li key={`reason-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {activeListing.match_missing && activeListing.match_missing.length > 0 && (
                  <div className="ai-match-group ai-match-warn">
                    <p className="ai-match-group-title">À vérifier</p>
                    <ul>
                      {activeListing.match_missing.map((item, index) => (
                        <li key={`missing-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {activeListing.match_dealbreakers && activeListing.match_dealbreakers.length > 0 && (
                  <div className="ai-match-group ai-match-bad">
                    <p className="ai-match-group-title">Points bloquants</p>
                    <ul>
                      {activeListing.match_dealbreakers.map((item, index) => (
                        <li key={`dealbreaker-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {activeListing.score_breakdown && activeListing.score_breakdown.length > 0 && (
              <div className="score-breakdown">
                <h3>Détail du score</h3>
                <ul>
                  {activeListing.score_breakdown.map((factor, index) => (
                    <li key={`${factor.label}-${index}`} className={factor.delta < 0 ? "malus" : "bonus"}>
                      <span className="factor-label">{factor.label}</span>
                      <span className="factor-delta">
                        {factor.delta > 0 ? `+${factor.delta}` : factor.delta}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="note-field">
              <label className="field-label" htmlFor="listing-note">
                Notes privées
              </label>
              <textarea
                id="listing-note"
                placeholder="Ajoute une note pour le groupe (visite prévue, points d'attention...)"
                value={noteDraft}
                onChange={(event) => setNoteDraft(event.target.value)}
              />
            </div>
            <div className="modal-actions">
              <button className="action-save" onClick={() => saveNote(activeListing.id, noteDraft)}>
                <Save size={18} />
                Enregistrer la note
              </button>
              <button className="action-favorite" onClick={() => setListingStatus(activeListing.id, "favorite", noteDraft)}>
                <Heart size={18} />
                Shortlist
              </button>
              <button className="action-call" onClick={() => setListingStatus(activeListing.id, "call", noteDraft)}>
                <Phone size={18} />
                Appeler
              </button>
              <button className="action-reject" onClick={() => setListingStatus(activeListing.id, "rejected", noteDraft)}>
                <X size={18} />
                Rejeter
              </button>
            </div>
          </section>
        </div>
      )}

      {selectedProfile && (
        <div className="modal-backdrop" onClick={() => setSelectedProfile(null)}>
          <section className="modal small" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" onClick={() => setSelectedProfile(null)}>
              <X size={18} />
            </button>
            <h2>
              <MapPin size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              {selectedProfile.city}
            </h2>
            <label className="field-label" htmlFor="profile-max-price">
              Budget max
            </label>
            <input
              id="profile-max-price"
              inputMode="numeric"
              value={selectedProfile.max_price_eur || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, max_price_eur: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-living-area">
              Surface habitable min
            </label>
            <input
              id="profile-living-area"
              inputMode="numeric"
              value={selectedProfile.min_living_area_m2 || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_living_area_m2: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-land-area">
              Terrain min
            </label>
            <input
              id="profile-land-area"
              inputMode="numeric"
              value={selectedProfile.min_land_area_m2 || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_land_area_m2: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-bedrooms">
              Chambres min
            </label>
            <input
              id="profile-bedrooms"
              inputMode="numeric"
              value={selectedProfile.min_bedrooms || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_bedrooms: event.target.value })}
            />
            <button className="primary" onClick={() => saveProfile(selectedProfile)}>
              <Save size={18} />
              Enregistrer
            </button>
          </section>
        </div>
      )}

      {selectedNaturalProfile && (
        <div className="modal-backdrop" onClick={() => setSelectedNaturalProfile(null)}>
          <section className="modal small" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" onClick={() => setSelectedNaturalProfile(null)}>
              <X size={18} />
            </button>
            <h2>
              <Sparkles size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              Recherche en langage naturel
            </h2>
            <label className="field-label" htmlFor="natural-profile-name">
              Nom
            </label>
            <input
              id="natural-profile-name"
              value={selectedNaturalProfile.name || ""}
              onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, name: event.target.value })}
            />
            <label className="field-label" htmlFor="natural-profile-prompt">
              Description libre
            </label>
            <textarea
              id="natural-profile-prompt"
              value={selectedNaturalProfile.raw_prompt || ""}
              onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, raw_prompt: event.target.value })}
            />
            <label className="field-label" htmlFor="natural-profile-active">
              <input
                id="natural-profile-active"
                type="checkbox"
                style={{ width: "auto", marginRight: 8 }}
                checked={!!selectedNaturalProfile.is_active}
                onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, is_active: event.target.checked })}
              />
              Recherche active
            </label>
            <button className="primary" onClick={() => saveNaturalProfile(selectedNaturalProfile)}>
              <Save size={18} />
              Enregistrer
            </button>
          </section>
        </div>
      )}

      {showComparison && (
        <div className="modal-backdrop" onClick={() => setShowComparison(false)}>
          <section className="modal compare-modal" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" onClick={() => setShowComparison(false)}>
              <X size={18} />
            </button>
            <h2>
              <Scale size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              Comparatif ({comparison.length}/{4})
            </h2>
            {comparison.length === 0 ? (
              <p className="compare-empty">
                Ajoute des annonces au comparatif depuis leurs cartes pour les retrouver ici.
              </p>
            ) : (
              <div className="compare-table-wrap">
                <table className="compare-table">
                  <thead>
                    <tr>
                      <th className="compare-row-label"></th>
                      {comparison.map((listing) => (
                        <th key={listing.id}>
                          <button
                            className="compare-remove"
                            title="Retirer du comparatif"
                            onClick={() => removeFromComparison(listing.id)}
                          >
                            <X size={14} />
                          </button>
                          <div className="compare-photo">
                            {listing.photos[0] ? <img src={listing.photos[0].url} alt={listing.title} /> : <Home size={26} />}
                          </div>
                          <p className="compare-title" onClick={() => openListing(listing)}>
                            {listing.title}
                          </p>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th className="compare-row-label">Prix</th>
                      {comparison.map((listing) => (
                        <td key={listing.id} className="compare-price">
                          {formatPrice(listing.price_eur)}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Prix / m²</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{pricePerM2(listing)}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Surface habitable</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.living_area_m2 ? `${listing.living_area_m2} m²` : "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Terrain</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.land_area_m2 ? `${listing.land_area_m2} m²` : "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Chambres</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.bedrooms ?? "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Ville</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          {listing.city} {listing.postal_code || ""}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Source</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          {listing.sources[0]?.url ? (
                            <a href={listing.sources[0].url} target="_blank" rel="noreferrer" className="source">
                              {listing.sources[0].source}
                            </a>
                          ) : (
                            "?"
                          )}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Score</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          <span className={`score-badge ${scoreTier(listing.score)}`}>{listing.score ?? "-"} / 100</span>
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Note privée</th>
                      {comparison.map((listing) => (
                        <td key={listing.id} className="compare-note">
                          {listing.note || "—"}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

createRoot(document.getElementById("root")).render(<App />);
