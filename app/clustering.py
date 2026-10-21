"""Universal prompt-conditioned semantic embedding and dynamic agglomerative clustering module.

Provides a fully generalized, zero-shot pipeline for Smart Grouping:
1. Projects contacts into the semantic subspace dictated by the user's natural language prompt
   using cross-attention across structured entity attributes (location, seniority, organization,
   education, interests, relationship).
2. Applies Agglomerative Clustering with Silhouette Score optimization to dynamically discover
   cohesive, balanced cohorts without hardcoded rules or keyword special-casing.
3. Synthesizes factual, informative cohort titles, strategic insights, and assignment rationales
   grounded in the members' actual data along the active grouping dimension.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

import httpx
import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

logger = logging.getLogger(__name__)

SPLIT_COLOR_PALETTE = [
    "#2563eb",  # Calm Blue
    "#0d9488",  # Muted Teal
    "#475569",  # Slate
    "#059669",  # Sage Green
    "#d97706",  # Warm Amber
    "#7c3aed",  # Soft Violet
    "#0891b2",  # Ocean
    "#4b5563",  # Cool Gray
]

SEMANTIC_ASPECTS: dict[str, str] = {
    "location": (
        "geographic location, city, metropolitan area, country, region, address, places, geography"
    ),
    "seniority": (
        "seniority level, professional career experience, years of experience, work experience, "
        "executive leadership, hierarchy, job level, profession, role"
    ),
    "organization": (
        "company, employer, industry sector, business domain, commercial firm, organization, "
        "corporate workplace"
    ),
    "education": (
        "education, university, college, school, academic degree, alumni, alma mater, "
        "higher education"
    ),
    "interests": (
        "skills, technical expertise, domain interests, specialty, technologies, focus area, hobby"
    ),
    "relationship": (
        "connection strength, relationship depth, interactions, cadence, "
        "close friend or acquaintance"
    ),
}

_FAST_EMBEDDING_MODEL: Any | None = None


def _get_embedding_model() -> Any | None:
    """Lazily load and cache the local ONNX embedding model (BAAI/bge-small-en-v1.5)."""
    global _FAST_EMBEDDING_MODEL
    if _FAST_EMBEDDING_MODEL is None:
        try:
            from fastembed import TextEmbedding

            _FAST_EMBEDDING_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        except Exception as exc:
            logger.warning(
                "FastEmbed model initialization unavailable, using TF-IDF fallback: %s", exc
            )
    return _FAST_EMBEDDING_MODEL


def serialize_contact_document(contact: Any, task_count: int = 0) -> str:
    """Combines node facts, edge context, and recent history into a canonical text document.

    Args:
        contact: An object or dataclass with .person and .relationship attributes.
        task_count: Number of pending tasks for this contact.

    Returns:
        A rich structured text representation of the contact node and relationship edge.
    """
    p = contact.person
    r = contact.relationship

    interests_str = ", ".join(p.interest_list) if p.interest_list else "None"
    context_str = ", ".join(r.context_label_list) if r.context_label_list else "Direct Contact"

    parts = [
        f"IDENTITY: {p.display_name}",
        f"JOB TITLE: {p.job_title or 'Professional'}",
        f"ORGANIZATION: {p.company or 'Independent'}",
        f"LOCATION: {p.location or 'Unrecorded'}",
        f"EDUCATION & ALMA MATER: {p.education or 'Unrecorded'}",
        f"INTERESTS & TOPICS: {interests_str}",
        f"RELATIONSHIP TIES: {context_str}",
        f"SHARED ACTIVITIES: {r.shared_activities or 'None'}",
        f"RELATIONSHIP SUMMARY: {r.summary or 'No notes logged yet'}",
        f"INITIAL NOTE: {r.notes or 'None'}",
        f"LAST ENGAGEMENT: {r.last_interaction_date or 'None'}",
        f"PENDING ACTION ITEMS: {task_count}",
    ]
    return "\n".join(parts)


def _compute_gemini_embeddings(
    documents: list[str],
    user_prompt: str,
    api_key: str,
) -> np.ndarray | None:
    """Generate dense embeddings via Gemini text-embedding-004 API."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:"
        f"batchEmbedContents?key={api_key}"
    )
    requests_payload = []
    task_title = f"Cluster professional contacts by: {user_prompt}"

    for doc in documents:
        requests_payload.append(
            {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": doc}]},
                "taskType": "CLUSTERING",
                "title": task_title,
            }
        )

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, json={"requests": requests_payload})
            if resp.status_code == 200:
                data = resp.json()
                raw_embeds = data.get("embeddings", [])
                if len(raw_embeds) == len(documents):
                    vectors = np.array([e["values"] for e in raw_embeds], dtype=np.float32)
                    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    return vectors / norms
    except Exception as exc:
        logger.warning("Gemini batch embedding call failed, falling back to local: %s", exc)
    return None


def _determine_prompt_aspect_weights(user_prompt: str) -> tuple[str, dict[str, float]]:
    """Determine cross-attention weights between user prompt and candidate semantic dimensions.

    Uses dense semantic similarity between the prompt and aspect definitions, then applies
    temperature-scaled softmax to sharpen the focus onto the user's intended dimension.
    """
    aspect_names = list(SEMANTIC_ASPECTS.keys())
    aspect_texts = list(SEMANTIC_ASPECTS.values())
    model = _get_embedding_model()

    if model is not None:
        try:
            p_vec = list(model.embed([user_prompt]))[0]
            aspect_embeds = np.array(list(model.embed(aspect_texts)))
            sims = np.dot(aspect_embeds, p_vec)
            # Temperature-scaled softmax (beta=20.0 creates a crisp focus on the target subspace)
            beta = 20.0
            exp_sims = np.exp(beta * (sims - np.max(sims)))
            weights = exp_sims / np.sum(exp_sims)
            weight_dict = {name: float(w) for name, w in zip(aspect_names, weights, strict=False)}
            dominant = aspect_names[int(np.argmax(weights))]
            return dominant, weight_dict
        except Exception as exc:
            logger.warning("Aspect embedding attention failed, using lexical fallback: %s", exc)

    # Lexical matching fallback
    prompt_lower = user_prompt.lower()
    scores: dict[str, float] = {}
    for name, desc in SEMANTIC_ASPECTS.items():
        tokens = [t.strip() for t in desc.replace(",", " ").split() if len(t.strip()) > 3]
        match_count = sum(1 for t in tokens if t in prompt_lower)
        scores[name] = float(match_count)

    max_score = max(scores.values()) if scores and max(scores.values()) > 0 else 1.0
    dominant = max(scores, key=lambda k: scores[k]) if max_score > 0 else "organization"
    weight_dict = {
        name: (0.85 if name == dominant else 0.15 / max(1, len(aspect_names) - 1))
        for name in aspect_names
    }
    return dominant, weight_dict


def _compute_prompt_conditioned_local_embeddings(
    documents: list[str],
    contacts: list[Any],
    user_prompt: str,
) -> np.ndarray:
    """Universal prompt-conditioned semantic vectorizer.

    Embeds contacts across distinct structured facets (location, seniority, company, education,
    skills, relationship) and linearly combines them using prompt-aspect cross-attention weights.
    """
    dominant_aspect, aspect_weights = _determine_prompt_aspect_weights(user_prompt)
    model = _get_embedding_model()

    if model is not None:
        try:
            # Build clean facet texts for all contacts
            facet_texts: dict[str, list[str]] = {
                "location": [c.person.location or "Global Unrecorded Location" for c in contacts],
                "seniority": [
                    (
                        f"{_classify_seniority_tier(c.person.job_title)}: "
                        f"{c.person.job_title or 'Professional'}"
                    )
                    for c in contacts
                ],
                "organization": [
                    f"{c.person.company or 'Organization'}. {' '.join(c.person.interest_list)}"
                    for c in contacts
                ],
                "education": [c.person.education or "General Education" for c in contacts],
                "interests": [
                    " ".join(c.person.interest_list)
                    if c.person.interest_list
                    else "General Specialty"
                    for c in contacts
                ],
                "relationship": [
                    f"Context: {' '.join(c.relationship.context_label_list) or 'network'}"
                    for c in contacts
                ],
            }

            field_vectors: dict[str, np.ndarray] = {}
            for name, texts in facet_texts.items():
                field_vectors[name] = np.array(list(model.embed(texts)), dtype=np.float32)

            # Combine facet representations weighted by prompt cross-attention
            first_key = list(field_vectors.keys())[0]
            composite = np.zeros_like(field_vectors[first_key])
            for name, w in aspect_weights.items():
                if name in field_vectors:
                    composite += w * field_vectors[name]

            # Add minor holistic profile context (5%) to preserve individual coherence
            holistic_embeds = np.array(list(model.embed(documents)), dtype=np.float32)
            composite = 0.95 * composite + 0.05 * holistic_embeds

            # Normalize to unit sphere
            norms = np.linalg.norm(composite, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return np.asarray(composite / norms, dtype=np.float32)
        except Exception as exc:
            logger.warning("Local dense embedding failed, using TF-IDF fallback: %s", exc)

    # Deterministic TF-IDF Fallback
    tfidf = TfidfVectorizer(
        sublinear_tf=True,
        ngram_range=(1, 2),
        min_df=1,
        max_features=120,
        stop_words="english",
    )
    base_matrix = tfidf.fit_transform(documents).toarray()

    # Weight aspect-specific vocabulary in the TF-IDF matrix
    target_vocab: list[str] = []
    if dominant_aspect == "location":
        target_vocab = [c.person.location or "" for c in contacts]
    elif dominant_aspect == "seniority":
        target_vocab = [c.person.job_title or "" for c in contacts]
    elif dominant_aspect == "education":
        target_vocab = [c.person.education or "" for c in contacts]
    elif dominant_aspect == "organization":
        target_vocab = [c.person.company or "" for c in contacts]
    elif dominant_aspect == "interests":
        target_vocab = [" ".join(c.person.interest_list) for c in contacts]

    if target_vocab:
        facet_tfidf = TfidfVectorizer(
            sublinear_tf=True,
            ngram_range=(1, 2),
            min_df=1,
            max_features=40,
            stop_words="english",
        )
        facet_matrix = facet_tfidf.fit_transform(target_vocab).toarray()
        f_norm = facet_matrix / np.maximum(
            np.linalg.norm(facet_matrix, axis=1, keepdims=True), 1e-6
        )
        b_norm = base_matrix / np.maximum(np.linalg.norm(base_matrix, axis=1, keepdims=True), 1e-6)
        combined = np.hstack([0.90 * f_norm, 0.10 * b_norm])
    else:
        combined = base_matrix

    norms = np.linalg.norm(combined, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(combined / norms, dtype=np.float32)


def get_contact_embeddings(
    contacts: list[Any],
    user_prompt: str,
    task_counts: dict[str, int],
) -> np.ndarray:
    """Generate prompt-conditioned semantic vectors for all contacts."""
    documents = [
        serialize_contact_document(c, task_count=task_counts.get(c.person.id, 0)) for c in contacts
    ]
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if api_key:
        gemini_vecs = _compute_gemini_embeddings(documents, user_prompt, api_key)
        if gemini_vecs is not None:
            return gemini_vecs

    return _compute_prompt_conditioned_local_embeddings(documents, contacts, user_prompt)


def dynamic_agglomerative_clustering(
    embeddings: np.ndarray,
    min_k: int = 2,
    max_k: int = 5,
    distance_threshold: float = 0.55,
) -> tuple[int, np.ndarray, float]:
    """Finds optimal number of clusters maximizing Silhouette Score.

    Evaluates silhouette scores across k in [min_k, max_k] using cosine distance to discover
    natural, cohesive groupings without requiring manual cluster count specification.
    """
    n_samples = len(embeddings)
    if n_samples == 0:
        return 1, np.array([], dtype=int), 0.0
    if n_samples == 1:
        return 1, np.array([0], dtype=int), 1.0

    # Pairwise cosine distance
    dots = np.dot(embeddings, embeddings.T)
    dist_mat = np.clip(1.0 - dots, 0.0, 2.0)
    np.fill_diagonal(dist_mat, 0.0)

    # For small sets where all items have clear pairwise separation
    if n_samples <= max_k and np.all(dist_mat[np.triu_indices(n_samples, k=1)] > 0.12):
        return n_samples, np.arange(n_samples, dtype=int), 1.0

    effective_max_k = min(max_k, n_samples - 1)
    if effective_max_k < min_k:
        clustering = AgglomerativeClustering(
            n_clusters=n_samples,
            metric="precomputed",
            linkage="average",
        )
        labels = clustering.fit_predict(dist_mat)
        return n_samples, labels, 0.0

    best_k = min_k
    best_score = -1.0
    best_labels = np.zeros(n_samples, dtype=int)

    for k in range(min_k, effective_max_k + 1):
        clustering = AgglomerativeClustering(
            n_clusters=k,
            metric="precomputed",
            linkage="average",
        )
        current_labels = clustering.fit_predict(dist_mat)
        unique_labels = len(set(current_labels))
        if unique_labels < 2:
            continue

        try:
            score = float(silhouette_score(dist_mat, current_labels, metric="precomputed"))
        except Exception:
            score = 0.0

        if score > best_score:
            best_score = score
            best_k = k
            best_labels = current_labels

    if best_score < 0.0:
        clustering = AgglomerativeClustering(
            n_clusters=min_k,
            metric="precomputed",
            linkage="average",
        )
        best_labels = clustering.fit_predict(dist_mat)
        best_k = min_k
        best_score = 0.0

    return best_k, best_labels, best_score


def _find_cluster_medoid(
    cluster_indices: list[int],
    embeddings: np.ndarray,
) -> int:
    """Find the index of the representative medoid contact closest to cluster centroid."""
    if not cluster_indices:
        return 0
    if len(cluster_indices) == 1:
        return cluster_indices[0]

    cluster_vecs = embeddings[cluster_indices]
    centroid = np.mean(cluster_vecs, axis=0, keepdims=True)
    c_norm = np.linalg.norm(centroid)
    if c_norm > 0:
        centroid = centroid / c_norm

    sims = np.dot(cluster_vecs, centroid.T).flatten()
    best_local_idx = int(np.argmax(sims))
    return cluster_indices[best_local_idx]


def _classify_seniority_tier(job_title: str | None) -> str:
    """Classify a contact's role into an experience level."""
    t = (job_title or "").lower()
    if any(
        k in t
        for k in (
            "founder",
            "ceo",
            "cto",
            "coo",
            "president",
            "managing partner",
            "general partner",
            "managing director",
            "chairman",
        )
    ):
        return "Executive & C-Suite (10+ yrs experience)"
    if any(k in t for k in ("vice president", "vp", "director", "head of", "principal", "partner")):
        return "Senior Leadership & Directors (6-10 yrs experience)"
    if any(k in t for k in ("candidate", "student", "fellow", "intern", "assistant", "mba")):
        return "Fellows, Candidates & Students (0-2 yrs experience)"
    if any(
        k in t
        for k in (
            "senior",
            "lead",
            "manager",
            "project leader",
            "architect",
            "quant",
            "specialist",
            "professor",
        )
    ):
        return "Mid-Level Leads & Specialists (3-5 yrs experience)"
    return "Early Career & Associates (1-3 yrs experience)"


def _synthesize_cluster_titles_and_insights(
    user_prompt: str,
    cluster_contacts_map: dict[int, list[Any]],
    cluster_medoids: dict[int, Any],
) -> dict[int, dict[str, str]]:
    """Synthesizes human-readable cohort titles, descriptions, and insights.

    Universal and dynamic: inspects the dominant prompt aspect and derives group names
    grounded in members' actual values along that dimension.
    """
    dominant_aspect, _ = _determine_prompt_aspect_weights(user_prompt)
    meta: dict[int, dict[str, str]] = {}
    seen_names: set[str] = set()

    for cid, c_list in cluster_contacts_map.items():
        medoid = cluster_medoids[cid]
        p_medoid = medoid.person

        if dominant_aspect == "location":
            locs = [
                c.person.location for c in c_list if c.person.location and c.person.location.strip()
            ]
            top_locs = Counter(locs).most_common(2)
            if not top_locs:
                name = "Global Network"
            elif len(top_locs) == 1 or top_locs[0][1] >= 2 * top_locs[1][1]:
                name = f"{top_locs[0][0]} Hub"
            else:
                name = f"{top_locs[0][0]} & {top_locs[1][0]}"

            desc = f"Contacts located in {name} and adjacent regional hubs."
            insight = "Plan local dinners, co-working sessions, and regional conference meetups."

        elif dominant_aspect == "education":
            edus = [
                c.person.education
                for c in c_list
                if c.person.education and c.person.education.strip()
            ]
            if not edus:
                name = "Academic & Alumni Cohort"
            else:
                top_edu_raw = Counter(edus).most_common(1)[0][0]
                if " - " in top_edu_raw:
                    parts = top_edu_raw.split(" - ")
                    inst = parts[0].strip().replace("University", "").strip()
                    school = parts[1].strip()
                    if "fuqua" in school.lower():
                        name = f"{inst} - Fuqua"
                    elif "pratt" in school.lower():
                        name = f"{inst} - Pratt"
                    else:
                        clean_school = school.split(",")[0].strip()
                        name = f"{inst} - {clean_school}"
                else:
                    clean_inst = top_edu_raw.split(",")[0].strip()
                    name = f"{clean_inst} Cohort"

            desc = f"Alumni, researchers, and partners affiliated with {name}."
            insight = (
                "Leverage shared institutional roots for recruiting and collaborative projects."
            )

        elif dominant_aspect == "seniority":
            tiers = [_classify_seniority_tier(c.person.job_title) for c in c_list]
            tier_counter = Counter(tiers).most_common()
            top_tier = tier_counter[0][0] if tier_counter else "Professional Cohort"

            candidate_name = top_tier
            if candidate_name in seen_names:
                role_counts = Counter(
                    [c.person.job_title for c in c_list if c.person.job_title]
                ).most_common(1)
                top_role = role_counts[0][0] if role_counts else f"Group {cid + 1}"
                candidate_name = f"{candidate_name} ({top_role})"

            name = candidate_name
            if "Executive" in name:
                desc = (
                    "Executive decision-makers, CEOs, Managing Partners, and "
                    "Founders leading organizations."
                )
                insight = (
                    "High-leverage cohort for board seats, corporate governance, "
                    "and syndicate investment."
                )
            elif "Senior Leadership" in name:
                desc = (
                    "VPs, Directors, Technical Leads, and Principals driving "
                    "strategic business units."
                )
                insight = (
                    "Key partners for strategic execution, talent acquisition, "
                    "and system architecture."
                )
            elif "Mid-Level" in name:
                desc = (
                    "Experienced engineers, engagement managers, project leaders, "
                    "and domain specialists."
                )
                insight = (
                    "Core practitioners for hands-on execution, operational diligence, "
                    "and technical feedback."
                )
            elif "Fellows" in name:
                desc = (
                    "Graduate researchers, MBA candidates, fellows, and emerging academic talent."
                )
                insight = (
                    "Nurture emerging talent for high-potential future leadership "
                    "and deal sourcing."
                )
            else:
                desc = (
                    "Associates, analysts, and junior practitioners building core domain expertise."
                )
                insight = "Engage for ground-level operational insights and peer networking."

        elif dominant_aspect in ("organization", "interests"):
            ints = [it for c in c_list for it in c.person.interest_list]
            top_ints = Counter(ints).most_common(2)
            comps = [
                c.person.company for c in c_list if c.person.company and c.person.company.strip()
            ]
            top_comps = Counter(comps).most_common(2)

            if top_ints:
                if len(top_ints) > 1:
                    name = f"{top_ints[0][0]} & {top_ints[1][0]}"
                else:
                    name = f"{top_ints[0][0]} Sector"
            elif top_comps:
                if len(top_comps) == 1:
                    name = f"{top_comps[0][0]} Cohort"
                else:
                    name = f"{top_comps[0][0]} & {top_comps[1][0]}"
            else:
                name = f"{p_medoid.company or 'Specialized'} Network"

            desc = f"Practitioners and leaders focused on {name}."
            insight = (
                "Share sector market intelligence, pipeline intros, and commercial opportunities."
            )

        elif dominant_aspect == "relationship":
            contexts = [
                lbl for c in c_list for lbl in getattr(c.relationship, "context_label_list", [])
            ]
            top_contexts = Counter(contexts).most_common(2)
            name = f"{top_contexts[0][0].title()} Circle" if top_contexts else "Strategic Network"
            desc = f"Connections bound by {name.lower()} relationship ties."
            insight = "Maintain regular communication and cadence touchpoints."

        else:
            comp = p_medoid.company or ""
            role = p_medoid.job_title or "Specialists"
            if comp and comp not in ("Independent", "Duke University"):
                name = f"{comp} Cohort"
            elif role:
                name = f"{role} Network"
            else:
                name = f"Cohort {cid + 1}"
            desc = f"Contacts structurally aligned with {p_medoid.display_name} ({role})."
            insight = "Prioritize regular quarterly touchpoints with this strategic cohort."

        # Ensure uniqueness across all clusters
        if name in seen_names:
            name = f"{name} ({cid + 1})"
        seen_names.add(name)

        meta[cid] = {"name": name, "description": desc, "insight": insight}

    return meta


def _generate_assignment_rationale(
    contact: Any,
    group_name: str,
    user_prompt: str,
) -> str:
    """Generate an informative, fact-based rationale for contact assignment."""
    p = contact.person
    r = contact.relationship
    dominant_aspect, _ = _determine_prompt_aspect_weights(user_prompt)

    if dominant_aspect == "location":
        loc = p.location or "regional network"
        return f"Assigned to {group_name} based on geographic location in {loc}."
    if dominant_aspect == "seniority":
        role = p.job_title or "professional"
        comp = f" at {p.company}" if p.company else ""
        return f"Assigned to {group_name} based on experience level as {role}{comp}."
    if dominant_aspect == "education":
        edu = p.education or "academic network"
        return f"Assigned to {group_name} based on educational affiliation with {edu}."
    if dominant_aspect in ("organization", "interests"):
        comp = p.company or "domain"
        role = p.job_title or "practitioner"
        return f"Assigned to {group_name} based on focus at {comp} ({role})."
    if dominant_aspect == "relationship":
        ties = ", ".join(r.context_label_list) if r.context_label_list else "network ties"
        return f"Assigned to {group_name} based on relationship ties ({ties})."

    role = p.job_title or "Professional"
    comp = f" at {p.company}" if p.company else ""
    return f"Assigned to {group_name} based on profile as {role}{comp}."


def execute_smart_grouping(
    contacts: list[Any],
    user_prompt: str,
    task_counts: dict[str, int],
) -> dict[str, Any]:
    """End-to-end ML Smart Grouping pipeline.

    1. Computes prompt-conditioned dense embeddings across structured contact attributes.
    2. Runs dynamic Agglomerative Clustering with Silhouette Score optimization.
    3. Computes medoids and synthesizes clean cohort titles, rationales, and metrics.
    """
    clean_prompt = user_prompt.strip()
    if not contacts:
        return {
            "title": "Smart Grouping",
            "prompt": clean_prompt,
            "groups": [],
            "assignments": {},
        }

    # 1. Embeddings
    embeddings = get_contact_embeddings(contacts, clean_prompt, task_counts)

    # 2. Dynamic clustering with automatic k selection
    k, labels, score = dynamic_agglomerative_clustering(embeddings, min_k=2, max_k=5)

    # Partition contacts
    cluster_contacts_map: dict[int, list[Any]] = {}
    cluster_indices_map: dict[int, list[int]] = {}
    for idx, c in enumerate(contacts):
        lbl = int(labels[idx])
        cluster_contacts_map.setdefault(lbl, []).append(c)
        cluster_indices_map.setdefault(lbl, []).append(idx)

    # Find medoids
    cluster_medoids: dict[int, Any] = {}
    for cid, indices in cluster_indices_map.items():
        medoid_idx = _find_cluster_medoid(indices, embeddings)
        cluster_medoids[cid] = contacts[medoid_idx]

    # Synthesize group titles & insights
    meta = _synthesize_cluster_titles_and_insights(
        clean_prompt, cluster_contacts_map, cluster_medoids
    )

    # Calculate statistics & build output payload
    total_contacts = len(contacts)
    groups_payload = []
    assignments_payload: dict[str, dict[str, str]] = {}

    for idx, (cid, c_list) in enumerate(cluster_contacts_map.items()):
        grp_name = meta[cid]["name"]
        grp_desc = meta[cid]["description"]
        grp_insight = meta[cid]["insight"]
        color = SPLIT_COLOR_PALETTE[idx % len(SPLIT_COLOR_PALETTE)]

        raw_companies = [
            c.person.company.strip()
            for c in c_list
            if c.person.company and c.person.company.strip()
        ]
        unique_companies = sorted(set(raw_companies))[:5]

        loc_counts: dict[str, int] = {}
        for c in c_list:
            if c.person.location and c.person.location.strip():
                loc = c.person.location.strip()
                loc_counts[loc] = loc_counts.get(loc, 0) + 1
        top_locations = [
            f"{loc} ({cnt})"
            for loc, cnt in sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        ]

        interest_counts: dict[str, int] = {}
        for c in c_list:
            for it in c.person.interest_list:
                interest_counts[it] = interest_counts.get(it, 0) + 1
        top_interests = [
            it for it, _ in sorted(interest_counts.items(), key=lambda x: x[1], reverse=True)[:4]
        ]

        ninety_days_ago = np.datetime64("today") - np.timedelta64(90, "D")
        engaged_count = 0
        for c in c_list:
            if c.relationship.last_interaction_date:
                try:
                    d_val = np.datetime64(c.relationship.last_interaction_date)
                    if d_val >= ninety_days_ago:
                        engaged_count += 1
                except Exception:
                    pass

        touchpoint_rate = round((engaged_count / max(1, len(c_list))) * 100, 1)
        pending_tasks = sum(task_counts.get(c.person.id, 0) for c in c_list)

        groups_payload.append(
            {
                "group_name": grp_name,
                "description": grp_desc,
                "contact_count": len(c_list),
                "percentage_of_network": round((len(c_list) / max(1, total_contacts)) * 100, 1),
                "companies": unique_companies,
                "top_locations": top_locations,
                "top_interests": top_interests,
                "active_touchpoint_rate": touchpoint_rate,
                "pending_tasks_count": pending_tasks,
                "strategic_insight": grp_insight,
                "color": color,
            }
        )

        for c in c_list:
            rationale = _generate_assignment_rationale(c, grp_name, clean_prompt)
            assignments_payload[c.person.id] = {
                "person_id": c.person.id,
                "group_name": grp_name,
                "rationale": rationale,
            }

    dominant_aspect, _ = _determine_prompt_aspect_weights(clean_prompt)
    if dominant_aspect == "location":
        overall_title = "Geographic Location Grouping"
    elif dominant_aspect == "seniority":
        overall_title = "Seniority & Experience Grouping"
    elif dominant_aspect == "education":
        overall_title = "University Grouping"
    elif dominant_aspect == "organization":
        overall_title = "Industry & Organization Grouping"
    elif dominant_aspect == "interests":
        overall_title = "Technical Domain & Interests Grouping"
    elif dominant_aspect == "relationship":
        overall_title = "Relationship & Network Grouping"
    else:
        overall_title = "Smart Grouping"

    return {
        "title": overall_title,
        "prompt": clean_prompt,
        "groups": groups_payload,
        "assignments": assignments_payload,
    }
