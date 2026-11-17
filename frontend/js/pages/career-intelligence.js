import { apiGet, apiPost } from '../services/api.js';

const loadingPanel = document.querySelector('[data-loading]');
const emptyPanel = document.querySelector('[data-empty]');
const errorPanel = document.querySelector('[data-error]');
const errorMessage = document.querySelector('[data-error-message]');
const dashboard = document.querySelector('[data-dashboard]');
const profileSummaryScreen = document.querySelector('[data-profile-summary-screen]');
const preferencesScreen = document.querySelector('[data-preferences-screen]');
const recommendationsScreen = document.querySelector('[data-recommendations-screen]');
const analysisScreen = document.querySelector('[data-analysis-screen]');
const preferencesForm = document.querySelector('[data-preferences-form]');
const preferencesValidation = document.querySelector('[data-preferences-validation]');
const preferencesNote = document.querySelector('[data-preferences-note]');
const recommendationsLoading = document.querySelector('[data-recommendations-loading]');
const recommendationsError = document.querySelector('[data-recommendations-error]');
const recommendationsErrorMessage = document.querySelector('[data-recommendations-error-message]');
const recommendationsErrorLink = document.querySelector('[data-recommendations-error-link]');
const recommendationsProfileLink = document.querySelector('[data-recommendations-profile-link]');
const recommendationsRetry = document.querySelector('[data-retry-recommendations]');
const recommendationsEmpty = document.querySelector('[data-recommendations-empty]');
const recommendationsStatus = document.querySelector('[data-recommendations-status]');
const topRecommendationsSection = document.querySelector('[data-top-recommendations]');
const topRecommendationResults = document.querySelector('[data-top-recommendation-results]');
const noEligibleRecommendations = document.querySelector('[data-no-eligible-recommendations]');
const topRankingMessage = document.querySelector('[data-top-ranking-message]');
const allEvaluatedSection = document.querySelector('[data-all-evaluated]');
const allEvaluatedRoles = document.querySelector('[data-all-evaluated-roles]');
const analysisSelectedRole = document.querySelector('[data-analysis-selected-role]');
const analysisLoading = document.querySelector('[data-analysis-loading]');
const analysisError = document.querySelector('[data-analysis-error]');
const analysisErrorMessage = document.querySelector('[data-analysis-error-message]');
const analysisSignIn = document.querySelector('[data-analysis-sign-in]');
const analysisProfileLink = document.querySelector('[data-analysis-profile-link]');
const analysisRetry = document.querySelector('[data-retry-analysis]');
const analysisResults = document.querySelector('[data-analysis-results]');
const aiConsentStatus = document.querySelector('[data-ai-consent-status]');
const aiConsentFeedback = document.querySelector('[data-ai-consent-feedback]');
const aiPrivacyDetails = document.querySelector('[data-ai-privacy-details]');
const aiConsentRetry = document.querySelector('[data-ai-consent-retry]');
const aiConsentUnsetActions = document.querySelector('[data-ai-consent-unset-actions]');
const aiConsentDisabledActions = document.querySelector('[data-ai-consent-disabled-actions]');
const aiConsentGrantedActions = document.querySelector('[data-ai-consent-granted-actions]');
const contextualPreferencesPanel = document.querySelector('[data-contextual-preferences]');
const contextualCareerGoal = document.querySelector('[data-contextual-career-goal]');
const contextualCareerPriority = document.querySelector('[data-contextual-career-priority]');
const recommendationSubmitButton = preferencesForm?.querySelector('button[type="submit"]');
const resultsEditPreferencesButton = document.querySelector('.ci-results-edit-preferences');
const stepItems = [...document.querySelectorAll('.ci-stepper [data-step]')];

const preferenceFields = ['career_interest', 'preferred_work_activity', 'career_goal', 'career_priority'];
const scoredComponentNames = ['skill_alignment', 'projects_experience', 'interest_alignment'];
const careerPreferencesState = Object.fromEntries(preferenceFields.map((field) => [field, '']));
let selectedAnalysisRoleId = null;
let selectedAnalysisRoleName = null;
let analysisRequestInProgress = false;
let aiConsentState = 'loading';
let aiConsentVersions = { notice: null, scope: null };
let aiConsentRequestInProgress = false;
window.careerPreferencesState = careerPreferencesState;
window.careerPreferences = null;

const educationLabels = {
  secondary: 'Secondary School',
  'higher-secondary': 'Higher Secondary / Pre-University',
  diploma: 'Diploma',
  undergraduate: 'Undergraduate',
  postgraduate: 'Postgraduate',
  doctorate: 'Doctorate',
  other: 'Other',
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setText(selector, value) {
  const target = document.querySelector(selector);
  if (target) target.textContent = value || '';
}

function setAIConsentControlsDisabled(disabled) {
  document.querySelectorAll('[data-ai-consent-grant], [data-ai-consent-decline], [data-ai-consent-enable], [data-ai-consent-withdraw]')
    .forEach((button) => { button.disabled = disabled; });
  aiConsentRetry.disabled = disabled;
}

function renderAIConsentStatus(message = '') {
  const enabled = aiConsentState === 'granted';
  const unavailable = aiConsentState === 'unavailable';
  const unset = aiConsentState === 'not_set';
  const needsReview = aiConsentState === 'needs_review';

  if (message) aiConsentStatus.textContent = message;
  else if (aiConsentState === 'loading') aiConsentStatus.textContent = 'Checking your AI privacy settings…';
  else if (enabled) aiConsentStatus.textContent = 'AI Personalization is enabled. You can withdraw permission at any time.';
  else if (unset) aiConsentStatus.textContent = 'AI Personalization is optional and is not enabled. Your deterministic career tools remain available.';
  else if (needsReview) aiConsentStatus.textContent = 'AI Personalization is off because the privacy notice has changed. Review the updated details before enabling it again.';
  else if (unavailable) aiConsentStatus.textContent = 'We could not confirm your consent status, so AI Personalization stays off. Your deterministic career tools remain available.';
  else aiConsentStatus.textContent = 'AI Personalization is off. You can enable it from these settings at any time.';

  aiConsentRetry.hidden = !unavailable;
  aiConsentUnsetActions.hidden = !unset;
  aiConsentDisabledActions.hidden = !(aiConsentState === 'declined' || aiConsentState === 'withdrawn' || needsReview);
  aiConsentGrantedActions.hidden = !enabled;
  if (unset) aiPrivacyDetails.open = true;
  if (enabled || aiConsentState === 'declined' || aiConsentState === 'withdrawn' || needsReview) aiPrivacyDetails.open = false;
  setAIConsentControlsDisabled(aiConsentRequestInProgress || aiConsentState === 'loading');
}

async function loadAIConsent() {
  if (aiConsentRequestInProgress) return;
  aiConsentState = 'loading';
  renderAIConsentStatus();
  try {
    const response = await apiGet('/api/ai-consent');
    const validStates = ['not_set', 'granted', 'declined', 'withdrawn'];
    if (!response || !validStates.includes(response.state)
      || typeof response.currentNoticeVersion !== 'string' || !response.currentNoticeVersion
      || typeof response.currentScopeVersion !== 'string' || !response.currentScopeVersion) {
      throw new Error('The consent status response was incomplete.');
    }
    aiConsentVersions = {
      notice: response.currentNoticeVersion,
      scope: response.currentScopeVersion,
    };
    aiConsentState = response.state;
    if (response.state === 'granted'
      && (response.noticeVersion !== response.currentNoticeVersion
        || response.scopeVersion !== response.currentScopeVersion)) {
      aiConsentState = 'needs_review';
    }
    renderAIConsentStatus();
  } catch (_error) {
    aiConsentState = 'unavailable';
    aiConsentVersions = { notice: null, scope: null };
    renderAIConsentStatus();
  }
}

async function saveAIConsentDecision(decision) {
  if (aiConsentRequestInProgress || !aiConsentVersions.notice || !aiConsentVersions.scope) return;
  aiConsentRequestInProgress = true;
  aiConsentFeedback.hidden = true;
  aiConsentStatus.textContent = decision === 'granted' ? 'Enabling AI Personalization…' : 'Saving your choice…';
  setAIConsentControlsDisabled(true);
  try {
    const response = await apiPost('/api/ai-consent', {
      decision,
      noticeVersion: aiConsentVersions.notice,
      scopeVersion: aiConsentVersions.scope,
    });
    if (!response || response.state !== decision) throw new Error('Consent could not be confirmed.');
    aiConsentState = response.state;
    aiConsentVersions = {
      notice: response.currentNoticeVersion,
      scope: response.currentScopeVersion,
    };
    renderAIConsentStatus(decision === 'granted'
      ? 'AI Personalization is enabled. Your deterministic career tools remain available.'
      : 'You chose to continue without AI. Your deterministic career tools remain available.');
  } catch (error) {
    const message = error.status === 401
      ? 'Please sign in again before changing AI privacy settings.'
      : 'We could not save this choice. Your existing setting is unchanged; please try again.';
    aiConsentFeedback.textContent = message;
    aiConsentFeedback.hidden = false;
    renderAIConsentStatus();
    aiPrivacyDetails.open = true;
  } finally {
    aiConsentRequestInProgress = false;
    setAIConsentControlsDisabled(aiConsentState === 'unavailable');
  }
}

async function withdrawAIConsent() {
  if (aiConsentRequestInProgress || aiConsentState !== 'granted') return;
  aiConsentRequestInProgress = true;
  aiConsentFeedback.hidden = true;
  aiConsentStatus.textContent = 'Withdrawing AI Personalization permission…';
  setAIConsentControlsDisabled(true);
  try {
    const response = await apiPost('/api/ai-consent/withdraw');
    if (!response || response.state !== 'withdrawn') throw new Error('Withdrawal could not be confirmed.');
    aiConsentState = 'withdrawn';
    renderAIConsentStatus('AI Personalization permission has been withdrawn. Your deterministic career tools remain available.');
  } catch (error) {
    aiConsentFeedback.textContent = error.status === 401
      ? 'Please sign in again before changing AI privacy settings.'
      : 'We could not withdraw permission. Your existing setting is unchanged; please try again.';
    aiConsentFeedback.hidden = false;
    renderAIConsentStatus();
    aiPrivacyDetails.open = true;
  } finally {
    aiConsentRequestInProgress = false;
    setAIConsentControlsDisabled(false);
  }
}

function setCount(selector, count, singular, plural = `${singular}s`) {
  setText(selector, `${count} ${count === 1 ? singular : plural}`);
}

function formatPoints(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value)
    : 'Not available';
}

function displayLabel(value) {
  return String(value).replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function roleDomainLabel(role) {
  const interestDetails = role.components?.interest_alignment?.details;
  const domainId = role.domain_id || role.role_domain_id || interestDetails?.role_domain_id;
  return domainId ? displayLabel(domainId) : 'Domain not provided';
}

function roleScoreLabel(role) {
  return typeof role.final_score === 'number' && Number.isFinite(role.final_score)
    ? `${formatPoints(role.final_score)} points`
    : 'Not available';
}

function detailedAnalysisButton(role) {
  const button = element('button', 'ci-button ci-button-secondary ci-analysis-select', 'View Career Analysis');
  button.type = 'button';
  button.dataset.analysisRoleId = role.role_id;
  button.setAttribute('aria-label', `View career analysis for ${role.role_name || 'this career role'}`);
  button.addEventListener('click', () => requestCareerAnalysis(role.role_id, role.role_name));
  return button;
}

function renderRecommendationRole(role, rank, aiExplanation = null) {
  const card = element('article', `ci-role-result${rank === 1 ? ' ci-role-result-first' : ''}`);
  card.id = `top-recommendation-${rank}`;
  const header = element('header', 'ci-role-result-heading');
  const identity = element('div', 'ci-role-identity');
  identity.append(element('span', 'ci-recommendation-rank', `Top match ${rank}`));
  identity.append(element('h3', '', role.role_name || 'Career role'));
  identity.append(element('p', 'ci-role-domain', `Domain: ${roleDomainLabel(role)}`));

  const score = element('div', 'ci-role-total-score');
  score.append(element('span', 'ci-score-label', 'Match score'));
  score.append(element('strong', '', roleScoreLabel(role)));
  header.append(identity, score);
  card.append(header);
  const fallbackExplanation = 'This career was selected as a top match based on the profile and preferences you shared.';
  const explanation = typeof aiExplanation === 'string' && aiExplanation.trim()
    ? aiExplanation.trim()
    : fallbackExplanation;
  card.append(element('p', 'ci-match-explanation', explanation));
  card.append(detailedAnalysisButton(role));
  return card;
}

function renderEvaluatedRole(role) {
  const item = element('article', 'ci-evaluated-role');
  const header = element('div', 'ci-evaluated-role-summary');
  const identity = element('div', 'ci-evaluated-role-identity');
  identity.append(element('h3', '', role.role_name || 'Career role'));
  identity.append(element('p', '', `Domain: ${roleDomainLabel(role)}`));
  header.append(identity);
  const score = element('div', 'ci-evaluated-role-score');
  score.append(element('span', 'ci-score-label', 'Match score'));
  score.append(element('strong', '', roleScoreLabel(role)));
  header.append(score);
  item.append(header);
  item.append(detailedAnalysisButton(role));
  return item;
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validCareerAnalysisResponse(data, requestedRoleId) {
  if (!isRecord(data) || !isRecord(data.role_overview)
    || data.role_overview.role_id !== requestedRoleId
    || typeof data.role_overview.name !== 'string'
    || typeof data.role_overview.domain !== 'string'
    || typeof data.role_overview.description !== 'string'
    || !Array.isArray(data.role_overview.typical_responsibilities)
    || data.role_overview.typical_responsibilities.some((item) => typeof item !== 'string')
    || !Array.isArray(data.role_overview.source_references)
    || data.role_overview.source_references.some((item) => !isRecord(item))) return false;

  const scoreSections = [data.skill_alignment, data.projects_experience, data.interest_alignment];
  if (scoreSections.some((section) => (
    !isRecord(section)
      || !(section.score === null || (typeof section.score === 'number' && Number.isFinite(section.score)))
      || typeof section.maximum_points !== 'number'
      || typeof section.maximum_applicable_points !== 'number'
      || !Number.isFinite(section.maximum_points)
      || !Number.isFinite(section.maximum_applicable_points)
      || typeof section.available !== 'boolean'
      || typeof section.status !== 'string'
      || (section.available && section.score === null)
      || (!section.available && section.score !== null)
      || !isRecord(section.details)
  ))) return false;

  return (data.skill_alignment.evidence_coverage_percentage === null
      || (typeof data.skill_alignment.evidence_coverage_percentage === 'number'
        && Number.isFinite(data.skill_alignment.evidence_coverage_percentage)))
    && typeof data.skill_alignment.requirements_interpretation === 'string'
    && Array.isArray(data.skill_alignment.matched_skills)
    && Array.isArray(data.skill_alignment.requirements_not_demonstrated_in_profile)
    && Array.isArray(data.skill_alignment.alternative_groups)
    && Array.isArray(data.skill_alignment.complementary_groups)
    && isRecord(data.skill_alignment.compatible_stacks)
    && Array.isArray(data.skill_alignment.conditional_exclusions)
    && isRecord(data.projects_experience.category_breakdown)
    && Array.isArray(data.projects_experience.inapplicable_categories)
    && isRecord(data.contextual_preferences)
    && isRecord(data.project_directions)
    && Array.isArray(data.project_directions.catalogue_suggestions)
    && isRecord(data.career_progression)
    && Array.isArray(data.career_progression.paths)
    && data.career_progression.paths.every((path) => Array.isArray(path) && path.every((item) => typeof item === 'string'))
    && Array.isArray(data.career_progression.related_roles)
    && typeof data.career_progression.note === 'string'
    && isRecord(data.availability)
    && typeof data.availability.status === 'string'
    && Array.isArray(data.availability.unavailable_reasons)
    && Array.isArray(data.availability.limitations);
}

function appendTextList(parent, heading, entries, emptyMessage = 'None provided in the catalogue.') {
  const section = element('section', 'ci-analysis-block');
  section.append(element('h3', '', heading));
  if (!entries.length) {
    section.append(element('p', 'ci-analysis-muted', emptyMessage));
  } else {
    const list = element('ul', 'ci-analysis-list');
    entries.forEach((entry) => list.append(element('li', '', entry)));
    section.append(list);
  }
  parent.append(section);
}

function uniqueNames(entries, field) {
  if (!Array.isArray(entries)) return [];
  const seen = new Set();
  return entries.flatMap((entry) => {
    const name = isRecord(entry) ? entry[field] : null;
    if (typeof name !== 'string' || !name.trim()) return [];
    const cleanName = name.trim();
    const normalized = cleanName.toLowerCase();
    if (seen.has(normalized)) return [];
    seen.add(normalized);
    return [cleanName];
  });
}

function projectEvidenceByCategory(categoryBreakdown) {
  if (!isRecord(categoryBreakdown)) return [];
  const labels = [
    ['projects', 'Projects'],
    ['internships', 'Internships'],
    ['professional_experience', 'Work experience'],
  ];
  return labels.flatMap(([key, label]) => {
    const category = categoryBreakdown[key];
    const names = uniqueNames(category?.matched_role_skills, 'name');
    return names.length ? [{ label, names }] : [];
  });
}

function referencedSkillId(item) {
  if (!isRecord(item)) return null;
  const value = typeof item.skill_id === 'string' ? item.skill_id : item.id;
  return typeof value === 'string' && value ? value : null;
}

function validAIStatements(entries, maxEntries, allowedSkillIds, allowedCategories) {
  if (!Array.isArray(entries) || !entries.length || entries.length > maxEntries) return null;
  const texts = [];
  for (const entry of entries) {
    if (!isRecord(entry) || typeof entry.text !== 'string'
      || entry.text.trim().length < 8 || entry.text.length > 300
      || !Array.isArray(entry.skill_ids) || !Array.isArray(entry.evidence_categories)
      || !Array.isArray(entry.project_indexes) || entry.project_indexes.length) return null;
    const skillIds = entry.skill_ids;
    const categories = entry.evidence_categories;
    if (new Set(skillIds).size !== skillIds.length
      || skillIds.some((id) => typeof id !== 'string' || !allowedSkillIds.has(id))
      || new Set(categories).size !== categories.length
      || categories.some((category) => typeof category !== 'string' || !allowedCategories.has(category))
      || (!skillIds.length && !categories.length)) return null;
    texts.push(entry.text.trim());
  }
  return texts;
}

function canonicalProjectSuggestionId(roleId, name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug ? `${roleId}::project::${slug}` : null;
}

function validatedCareerAnalysisEnrichment(data) {
  const enrichment = data?.ai_enrichment;
  if (!isRecord(enrichment) || enrichment.available !== true || !isRecord(enrichment.content)) return null;
  const content = enrichment.content;
  const matched = Array.isArray(data.skill_alignment.matched_skills) ? data.skill_alignment.matched_skills : [];
  const gaps = Array.isArray(data.skill_alignment.requirements_not_demonstrated_in_profile)
    ? data.skill_alignment.requirements_not_demonstrated_in_profile : [];
  const matchedIds = new Set(matched.map(referencedSkillId).filter(Boolean));
  const gapIds = new Set(gaps.map(referencedSkillId).filter(Boolean));
  const categoryBreakdown = data.projects_experience.category_breakdown;
  const evidenceCategories = new Set(['projects', 'internships', 'professional_experience'].filter((key) => (
    isRecord(categoryBreakdown[key])
      && Array.isArray(categoryBreakdown[key].matched_role_skills)
      && categoryBreakdown[key].matched_role_skills.length > 0
  )));

  const suggestions = Array.isArray(data.project_directions.catalogue_suggestions)
    ? data.project_directions.catalogue_suggestions.filter((name) => typeof name === 'string' && name.trim())
    : [];
  const projectsById = new Map(suggestions.map((name) => [
    canonicalProjectSuggestionId(data.role_overview.role_id, name), name.trim(),
  ]).filter(([id]) => id));
  let projects = null;
  const projectEntries = content.practice_project_suggestions;
  if (Array.isArray(projectEntries) && projectEntries.length > 0 && projectEntries.length <= 3) {
    const seen = new Set();
    const valid = projectEntries.every((item) => {
      if (!isRecord(item) || typeof item.project_id !== 'string' || seen.has(item.project_id)
        || !projectsById.has(item.project_id) || !isRecord(item.explanation)) return false;
      seen.add(item.project_id);
      return true;
    });
    if (valid) {
      const mapped = projectEntries.map((item) => ({
        name: projectsById.get(item.project_id),
        text: item.explanation.text,
        skill_ids: item.explanation.skill_ids,
        evidence_categories: item.explanation.evidence_categories,
        project_indexes: [],
      }));
      const texts = validAIStatements(mapped, 3, new Set([...matchedIds, ...gapIds]), evidenceCategories);
      if (texts) projects = mapped.map((item, index) => `${item.name}: ${texts[index]}`);
    }
  }

  return {
    why: validAIStatements(content.why_it_may_suit, 5, matchedIds, evidenceCategories),
    strengths: validAIStatements(content.strengths, 5, matchedIds, evidenceCategories),
    areas: validAIStatements(content.areas_to_explore, 8, gapIds, new Set()),
    projects,
  };
}

function renderCareerAnalysis(data) {
  analysisResults.replaceChildren();
  const role = data.role_overview;
  const ai = validatedCareerAnalysisEnrichment(data);
  const matchedSkills = uniqueNames(data.skill_alignment.matched_skills, 'skill_name');
  const projectEvidence = projectEvidenceByCategory(data.projects_experience.category_breakdown);
  const interestDetails = isRecord(data.interest_alignment.details) ? data.interest_alignment.details : {};

  const about = element('section', 'ci-analysis-card');
  about.append(element('h2', '', 'About This Career'));
  about.append(element('h3', 'ci-analysis-role-name', role.name));
  about.append(element('p', 'ci-analysis-domain', role.domain));
  about.append(element('p', 'ci-analysis-copy', role.description));
  appendTextList(about, 'Main responsibilities', role.typical_responsibilities.slice(0, 3), 'No responsibility details are available for this career yet.');
  analysisResults.append(about);

  const why = element('section', 'ci-analysis-card');
  why.append(element('h2', '', 'Why This Career May Suit You'));
  const reasons = [];
  if (interestDetails.career_interest_status === 'mapped'
    && interestDetails.match_status === 'matched'
    && typeof interestDetails.career_interest_option_label === 'string'
    && interestDetails.career_interest_option_label.trim()) {
    reasons.push(`Your selected career area, ${interestDetails.career_interest_option_label.trim()}, aligns with this role's field.`);
  }
  if (['matched', 'partial'].includes(interestDetails.preferred_work_activity_match_status)
    && typeof interestDetails.preferred_work_activity_option_label === 'string'
    && interestDetails.preferred_work_activity_option_label.trim()) {
    reasons.push(`Your preferred activity, ${interestDetails.preferred_work_activity_option_label.trim()}, has a reviewed connection to this role.`);
  }
  if (matchedSkills.length) {
    reasons.push(`Your profile lists relevant skills such as ${matchedSkills.slice(0, 4).join(', ')}.`);
  }
  const structuredEvidenceNames = uniqueNames(projectEvidence.flatMap((category) => category.names.map((name) => ({ name }))), 'name');
  if (structuredEvidenceNames.length) {
    reasons.push(`Your structured project or experience entries include relevant technologies such as ${structuredEvidenceNames.slice(0, 4).join(', ')}.`);
  }
  appendTextList(why, ai?.why ? 'Personalized explanation' : 'Based on your profile and preferences', ai?.why || reasons,
    'The available profile and preference information does not establish a specific match yet. You can still explore this career.');
  analysisResults.append(why);

  const strengths = element('section', 'ci-analysis-card');
  strengths.append(element('h2', '', 'Your Existing Strengths'));
  if (ai?.strengths) appendTextList(strengths, 'Personalized perspective', ai.strengths);
  appendTextList(strengths, 'Relevant skills in your profile', matchedSkills.slice(0, 7),
    'No role-related skills were matched from the skills currently listed in your profile.');
  if (matchedSkills.length > 7) {
    strengths.append(element('p', 'ci-analysis-muted', `And ${matchedSkills.length - 7} more relevant skills are listed in your profile.`));
  }
  const experienceSection = element('section', 'ci-analysis-block');
  experienceSection.append(element('h3', '', 'Relevant project and experience evidence'));
  if (!projectEvidence.length) {
    experienceSection.append(element('p', 'ci-analysis-muted', 'No matching evidence was found in the structured project, internship, or work experience fields in your saved profile.'));
  } else {
    projectEvidence.forEach(({ label, names }) => {
      const group = element('div', 'ci-analysis-evidence-group');
      group.append(element('h4', '', label));
      group.append(element('p', 'ci-analysis-muted', names.join(', ')));
      experienceSection.append(group);
    });
    experienceSection.append(element('p', 'ci-analysis-muted', 'These matches use the technologies recorded in structured profile fields.'));
  }
  strengths.append(experienceSection);
  analysisResults.append(strengths);

  const improvement = element('section', 'ci-analysis-card');
  improvement.append(element('h2', '', 'Areas You Can Improve'));
  if (ai?.areas) appendTextList(improvement, 'Personalized guidance', ai.areas);
  improvement.append(element('p', 'ci-analysis-caution', 'These role requirements are not currently shown in your profile. That is not a judgment about your ability; you can add relevant evidence or explore these topics.'));
  const requirements = Array.isArray(data.skill_alignment.requirements_not_demonstrated_in_profile)
    ? data.skill_alignment.requirements_not_demonstrated_in_profile
    : [];
  const requirementNames = uniqueNames(requirements, 'skill_name');
  appendTextList(improvement, 'Role requirements to explore', requirementNames.slice(0, 7),
    requirements.length
      ? 'Some requirements are not available by name in this analysis, so they are not listed here.'
      : 'No additional applicable requirements were identified as not shown in your profile.');
  if (requirementNames.length && requirementNames.length < requirements.length) {
    improvement.append(element('p', 'ci-analysis-muted', 'Other requirements in the analysis do not include display names, so they are omitted here.'));
  }
  if (requirementNames.length > 7) {
    improvement.append(element('p', 'ci-analysis-muted', `And ${requirementNames.length - 7} more role requirements may be useful to explore.`));
  }
  analysisResults.append(improvement);

  const projects = element('section', 'ci-analysis-card');
  projects.append(element('h2', '', 'Recommended Practice Projects'));
  projects.append(element('p', 'ci-analysis-muted', 'These are ideas to try, not projects recorded as completed in your profile.'));
  const deterministicSuggestions = Array.isArray(data.project_directions.catalogue_suggestions)
    ? data.project_directions.catalogue_suggestions.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3)
    : [];
  const suggestions = ai?.projects || deterministicSuggestions;
  appendTextList(projects, 'Project ideas to try', suggestions, 'No curated project ideas are available for this role yet.');
  analysisResults.append(projects);

  const roadmap = element('section', 'ci-analysis-card ci-analysis-roadmap');
  roadmap.append(element('h2', '', 'Continue to Career Roadmap'));
  roadmap.append(element('p', 'ci-analysis-muted', 'Continue with this career as your selected focus.'));
  const roadmapLink = element('a', 'ci-button ci-button-primary', 'Continue to Career Roadmap');
  roadmapLink.href = `roadmap.html?role_id=${encodeURIComponent(role.role_id)}`;
  roadmap.append(roadmapLink);
  analysisResults.append(roadmap);
}

function analysisErrorForStatus(status) {
  if (status === 401) return 'Your sign-in session may have expired. Sign in, then retry this analysis.';
  if (status === 404) return 'Your saved profile could not be found. Save Profile Setup, then retry.';
  if (status === 422) return 'The selected role or one of the preference selections was not accepted. Return to recommendations and review your selections.';
  if (status === 500) return 'The analysis service could not prepare this role right now. You can retry without restarting recommendations.';
  return 'The analysis response was unavailable or incomplete. Check your connection and retry.';
}

function showAnalysisError(status) {
  analysisErrorMessage.textContent = analysisErrorForStatus(status);
  analysisSignIn.hidden = status !== 401;
  analysisProfileLink.hidden = status !== 404;
  analysisError.hidden = false;
}

function showCareerAnalysisScreen() {
  analysisScreen.hidden = false;
  recommendationsScreen.hidden = true;
  preferencesScreen.hidden = true;
  profileSummaryScreen.hidden = true;
  setActiveStep(4);
  window.scrollTo(0, 0);
  analysisScreen.querySelector('h1').focus({ preventScroll: true });
}

function setAnalysisActionDisabled(disabled) {
  document.querySelectorAll('[data-analysis-role-id]').forEach((button) => { button.disabled = disabled; });
  analysisRetry.disabled = disabled;
}

async function requestCareerAnalysis(roleId = selectedAnalysisRoleId, roleName = null) {
  if (analysisRequestInProgress || !roleId) return;
  selectedAnalysisRoleId = roleId;
  if (typeof roleName === 'string' && roleName) selectedAnalysisRoleName = roleName;
  const preferences = window.careerPreferences;
  if (!preferences || !preferenceFields.every((field) => typeof preferences[field] === 'string' && preferences[field].trim())) {
    analysisResults.replaceChildren();
    analysisLoading.hidden = true;
    analysisError.hidden = true;
    analysisSelectedRole.textContent = selectedAnalysisRoleName || 'Selected career role';
    showCareerAnalysisScreen();
    showAnalysisError(422);
    analysisErrorMessage.textContent = 'Your four preferences are no longer available in this browser session. Return to Career Recommendations and review your selections.';
    return;
  }

  analysisRequestInProgress = true;
  analysisResults.replaceChildren();
  analysisError.hidden = true;
  analysisLoading.hidden = false;
  analysisSelectedRole.textContent = `Analysing ${selectedAnalysisRoleName || 'selected career role'}…`;
  setAnalysisActionDisabled(true);
  showCareerAnalysisScreen();

  try {
    const response = await apiPost('/api/career-analysis', {
      role_id: roleId,
      career_interest: preferences.career_interest,
      preferred_work_activity: preferences.preferred_work_activity,
      career_goal: preferences.career_goal,
      career_priority: preferences.career_priority,
    });
    if (!validCareerAnalysisResponse(response, roleId)) throw new Error('Malformed career analysis response.');
    analysisSelectedRole.textContent = `${response.role_overview.name} · ${response.role_overview.domain}`;
    renderCareerAnalysis(response);
  } catch (error) {
    const errorStatus = Number.isInteger(error?.status) ? error.status : null;
    showAnalysisError(errorStatus);
  } finally {
    analysisLoading.hidden = true;
    analysisRequestInProgress = false;
    setAnalysisActionDisabled(false);
  }
}

function contextualPreferenceLabel(preferences, field) {
  if (preferences === null || preferences === undefined) return 'Not provided';
  if (typeof preferences !== 'object' || Array.isArray(preferences)) return 'Not available';
  const option = preferences[field];
  if (option === null || option === undefined) return 'Not provided';
  if (typeof option !== 'object' || Array.isArray(option)) return 'Not available';
  return typeof option.label === 'string' && option.label.trim()
    ? option.label.trim()
    : 'Not available';
}

function renderContextualPreferences(response) {
  contextualCareerGoal.textContent = contextualPreferenceLabel(response?.contextual_preferences, 'career_goal');
  contextualCareerPriority.textContent = contextualPreferenceLabel(response?.contextual_preferences, 'career_priority');
  contextualPreferencesPanel.hidden = false;
}

function renderRecommendations(response) {
  if (!Array.isArray(response?.roles)) throw new Error('The recommendation response was incomplete.');
  if (response.roles.length !== 0 && response.roles.length !== 22) {
    throw new Error('The recommendation response did not contain all career roles.');
  }
  const requiredComponents = scoredComponentNames;
  if (response.roles.some((role) => (
    !role || typeof role !== 'object' || !role.components || typeof role.components !== 'object'
      || requiredComponents.some((name) => !role.components[name])
  ))) {
    throw new Error('The recommendation response did not contain all scoring components.');
  }
  topRecommendationResults.replaceChildren();
  allEvaluatedRoles.replaceChildren();
  topRankingMessage.hidden = true;
  noEligibleRecommendations.hidden = true;
  renderContextualPreferences(response);
  recommendationsEmpty.hidden = response.roles.length !== 0;
  recommendationsStatus.hidden = true;
  recommendationsStatus.textContent = '';
  if (!response.roles.length) return;

  const rolesById = new Map();
  response.roles.forEach((role) => {
    if (typeof role.role_id !== 'string' || !role.role_id.trim() || rolesById.has(role.role_id)) {
      throw new Error('The recommendation response contained invalid role identifiers.');
    }
    rolesById.set(role.role_id, role);
  });

  topRecommendationsSection.hidden = false;
  allEvaluatedSection.hidden = false;
  let topIds = response.top_recommendation_role_ids;
  if (!Array.isArray(topIds)) {
    topIds = [];
    topRankingMessage.textContent = 'Recommendation ranking details are unavailable. All evaluated roles remain listed below.';
    topRankingMessage.hidden = false;
  } else if (topIds.length > 3) {
    topIds = topIds.slice(0, 3);
    topRankingMessage.textContent = 'The response contained more than three recommendation IDs. Showing the first three in the order supplied.';
    topRankingMessage.hidden = false;
  }
  const seenTopIds = new Set();
  const resolvedTopRoles = [];
  let unresolvedCount = 0;
  topIds.forEach((roleId, position) => {
    if (typeof roleId !== 'string' || !roleId.trim() || seenTopIds.has(roleId)) {
      unresolvedCount += 1;
      return;
    }
    seenTopIds.add(roleId);
    const role = rolesById.get(roleId);
    if (!role) {
      unresolvedCount += 1;
      return;
    }
    resolvedTopRoles.push({ role, rank: position + 1 });
  });

  if (unresolvedCount) {
    topRankingMessage.textContent = resolvedTopRoles.length
      ? 'Some recommendation entries could not be matched to evaluated roles. The valid recommendations and full evaluated list are shown.'
      : 'The recommendation IDs could not be matched to evaluated roles. The full evaluated list remains available below.';
    topRankingMessage.hidden = false;
  }
  if (Array.isArray(response.top_recommendation_role_ids) && response.top_recommendation_role_ids.length === 0) {
    noEligibleRecommendations.hidden = false;
  }
  const aiExplanations = new Map();
  const aiEnrichment = response.ai_enrichment;
  if (isRecord(aiEnrichment) && aiEnrichment.available === true && Array.isArray(aiEnrichment.roles)
    && aiEnrichment.roles.length === resolvedTopRoles.length) {
    const validEntries = aiEnrichment.roles.every((item, index) => (
      isRecord(item)
      && item.role_id === resolvedTopRoles[index]?.role.role_id
      && isRecord(item.explanation)
      && typeof item.explanation.text === 'string'
      && item.explanation.text.trim().length > 0
      && item.explanation.text.length <= 300
    ));
    if (validEntries) {
      aiEnrichment.roles.forEach((item) => aiExplanations.set(item.role_id, item.explanation.text));
    }
  }
  resolvedTopRoles.forEach(({ role, rank }) => {
    topRecommendationResults.append(renderRecommendationRole(role, rank, aiExplanations.get(role.role_id)));
  });

  const recommendedIds = new Set(resolvedTopRoles.map(({ role }) => role.role_id));
  const otherRoles = response.roles.filter((role) => !recommendedIds.has(role.role_id));
  const exploreSummary = allEvaluatedSection.querySelector('summary');
  if (exploreSummary) exploreSummary.textContent = `Explore Other Careers (${otherRoles.length})`;
  otherRoles.forEach((role) => {
    allEvaluatedRoles.append(renderEvaluatedRole(role));
  });
  recommendationsStatus.textContent = `${resolvedTopRoles.length} top career match${resolvedTopRoles.length === 1 ? '' : 'es'} shown${otherRoles.length ? `, with ${otherRoles.length} other evaluated careers to explore.` : '.'}`;
  recommendationsStatus.hidden = false;
}

function recommendationErrorForStatus(status) {
  if (status === 401) return 'Your sign-in session has expired. Sign in again, then return to your preferences.';
  if (status === 404) return 'Your saved profile could not be found. Complete or save Profile Setup, then try again.';
  if (status === 422) return 'One or more preference selections could not be accepted. Review your selections and submit again.';
  if (status === 500) return 'The recommendation service could not calculate results right now. Please try again.';
  return 'We could not load complete recommendation results. Check your connection and try again.';
}

let recommendationRequestInProgress = false;

async function requestRecommendations(answers) {
  if (recommendationRequestInProgress) return;
  recommendationRequestInProgress = true;
  if (recommendationSubmitButton) recommendationSubmitButton.disabled = true;
  if (resultsEditPreferencesButton) resultsEditPreferencesButton.disabled = true;
  preferencesForm.setAttribute('aria-busy', 'true');
  recommendationsLoading.hidden = false;
  recommendationsError.hidden = true;
  recommendationsEmpty.hidden = true;
  recommendationsStatus.hidden = true;
  recommendationsStatus.textContent = '';
  contextualPreferencesPanel.hidden = true;
  topRecommendationsSection.hidden = true;
  noEligibleRecommendations.hidden = true;
  topRankingMessage.hidden = true;
  allEvaluatedSection.hidden = true;
  topRecommendationResults.replaceChildren();
  allEvaluatedRoles.replaceChildren();

  try {
    const response = await apiPost('/api/recommendations', {
      career_interest: answers.career_interest,
      preferred_work_activity: answers.preferred_work_activity,
      career_goal: answers.career_goal,
      career_priority: answers.career_priority,
    });
    recommendationsLoading.hidden = true;
    renderRecommendations(response);
  } catch (error) {
    recommendationsLoading.hidden = true;
    recommendationsEmpty.hidden = true;
    recommendationsStatus.hidden = true;
    recommendationsStatus.textContent = '';
    contextualPreferencesPanel.hidden = true;
    topRecommendationsSection.hidden = true;
    noEligibleRecommendations.hidden = true;
    topRankingMessage.hidden = true;
    allEvaluatedSection.hidden = true;
    topRecommendationResults.replaceChildren();
    allEvaluatedRoles.replaceChildren();
    const errorStatus = Number.isInteger(error?.status) ? error.status : null;
    recommendationsErrorMessage.textContent = recommendationErrorForStatus(errorStatus);
    recommendationsErrorLink.hidden = errorStatus !== 401;
    recommendationsProfileLink.hidden = errorStatus !== 404;
    recommendationsRetry.hidden = [401, 404, 422].includes(errorStatus);
    recommendationsError.hidden = false;
  } finally {
    recommendationsLoading.hidden = true;
    recommendationRequestInProgress = false;
    preferencesForm.removeAttribute('aria-busy');
    if (recommendationSubmitButton) recommendationSubmitButton.disabled = false;
    if (resultsEditPreferencesButton) resultsEditPreferencesButton.disabled = false;
  }
}

function toggleRows(button, nodes, collapsedLimit) {
  const expandable = nodes.length > collapsedLimit;
  button.hidden = !expandable;
  if (!expandable) return;
  let expanded = false;
  nodes.forEach((node, index) => { node.hidden = index >= collapsedLimit; });
  button.addEventListener('click', () => {
    expanded = !expanded;
    nodes.forEach((node, index) => { node.hidden = !expanded && index >= collapsedLimit; });
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? 'Show Less' : button.dataset.collapsedLabel;
  });
}

function toggleDetails(button, detailNodes) {
  const expandable = detailNodes.length > 0;
  button.hidden = !expandable;
  if (!expandable) return;
  let expanded = false;
  detailNodes.forEach((node) => { node.hidden = true; });
  button.addEventListener('click', () => {
    expanded = !expanded;
    detailNodes.forEach((node) => { node.hidden = !expanded; });
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? 'Hide Details' : button.dataset.collapsedLabel;
  });
}

function renderEducation(profile) {
  const basic = profile.basicInformation || {};
  const target = document.querySelector('[data-education-list]');
  const degree = basic.degree === 'other' ? basic.degreeOther : basic.degree;
  const field = basic.fieldOfStudy === 'other' ? basic.fieldOfStudyOther : basic.fieldOfStudy;
  const level = basic.educationLevel === 'other' ? basic.educationLevelOther : educationLabels[basic.educationLevel] || basic.educationLevel;
  const hasEducation = Boolean(degree || field || level);

  if (!hasEducation) {
    target.append(element('p', 'ci-empty-copy', 'No education details added yet.'));
    return 0;
  }

  const primary = [degree, field].filter(Boolean).join(' in ');
  if (primary) target.append(element('p', 'ci-education-primary', primary));
  const details = [];
  if (level) details.push(['Education level', level]);
  if (field && degree) details.push(['Field of study', field]);
  [
    ['Institution', basic.institution || basic.college || basic.university],
    ['Graduation year', basic.graduationYear || basic.graduation_year],
    ['Grade', basic.cgpa || basic.grade],
  ].forEach(([label, value]) => { if (value) details.push([label, value]); });

  const detailNodes = details.map(([label, value]) => {
    const line = element('p', 'ci-education-line');
    const strong = element('strong', '', `${label}: `);
    line.append(strong, document.createTextNode(String(value)));
    target.append(line);
    return line;
  });
  const button = document.querySelector('[data-toggle="education"]');
  toggleDetails(button, detailNodes);
  return degree ? 1 : 0;
}

function renderSkills(skills) {
  const target = document.querySelector('[data-skills-list]');
  const unique = [];
  const seen = new Set();
  (Array.isArray(skills) ? skills : []).forEach((value) => {
    const skill = String(value || '').trim();
    const key = skill.toLocaleLowerCase();
    if (skill && !seen.has(key)) { seen.add(key); unique.push(skill); }
  });
  const chips = unique.map((skill) => element('span', 'ci-chip', skill));
  chips.forEach((chip) => target.append(chip));
  if (!chips.length) target.append(element('p', 'ci-empty-copy', 'No skills added yet.'));
  const button = document.querySelector('[data-toggle="skills"]');
  button.dataset.collapsedLabel = 'View All';
  if (chips.length > 10) {
    const moreIndicator = element('span', 'ci-chip ci-chip-more', `+${chips.length - 10} more`);
    target.append(moreIndicator);
    let expanded = false;
    chips.forEach((chip, index) => { chip.hidden = index >= 10; });
    button.hidden = false;
    button.addEventListener('click', () => {
      expanded = !expanded;
      chips.forEach((chip, index) => { chip.hidden = !expanded && index >= 10; });
      moreIndicator.hidden = expanded;
      button.setAttribute('aria-expanded', String(expanded));
      button.textContent = expanded ? 'Show Less' : button.dataset.collapsedLabel;
    });
  } else {
    button.hidden = true;
  }
  return unique.length;
}

function renderExperiences(career) {
  const target = document.querySelector('[data-experience-list]');
  const records = [
    ...(Array.isArray(career.internshipExperience) ? career.internshipExperience.map((item) => ({ ...item, type: 'Internship' })) : []),
    ...(Array.isArray(career.professionalExperience) ? career.professionalExperience.map((item) => ({ ...item, type: 'Professional Experience' })) : []),
  ];
  if (!records.length) {
    target.append(element('p', 'ci-empty-copy', 'No experience added yet.'));
    return 0;
  }

  const nodes = records.map((record) => {
    const entry = element('article', 'ci-experience-entry');
    entry.append(element('h3', 'ci-entry-title', record.role || record.type));
    if (record.company) entry.append(element('p', 'ci-entry-subtitle', record.company));
    entry.append(element('p', 'ci-entry-meta', record.type + (record.duration ? ` · ${record.duration}` : '')));
    if (record.responsibilities) entry.append(element('p', 'ci-entry-description', record.responsibilities));
    if (record.technologies) entry.append(element('p', 'ci-entry-meta', `Technologies: ${record.technologies}`));
    target.append(entry);
    return entry;
  });
  const button = document.querySelector('[data-toggle="experience"]');
  button.dataset.collapsedLabel = 'View Details ›';
  toggleRows(button, nodes, 1);
  return records.length;
}

function renderProjects(projects) {
  const target = document.querySelector('[data-project-list]');
  const records = Array.isArray(projects) ? projects : [];
  const nodes = records.map((project) => {
    const item = element('li');
    item.append(element('strong', '', project.name || ''));
    if (project.description) item.append(element('span', 'ci-list-detail', project.description));
    if (project.technologies) item.append(element('span', 'ci-list-detail', `Technologies: ${project.technologies}`));
    target.append(item);
    return item;
  });
  if (!nodes.length) target.append(element('li', 'ci-empty-item', 'No projects added yet.'));
  const button = document.querySelector('[data-toggle="projects"]');
  button.dataset.collapsedLabel = 'View All';
  toggleRows(button, nodes, 3);
  return nodes.length;
}

function renderCertifications(certifications) {
  const target = document.querySelector('[data-certification-list]');
  const records = Array.isArray(certifications) ? certifications : [];
  const nodes = records.map((certification) => {
    const item = element('li');
    item.append(element('strong', '', certification.name || ''));
    if (certification.organization) item.append(element('span', 'ci-list-detail', certification.organization));
    target.append(item);
    return item;
  });
  if (!nodes.length) target.append(element('li', 'ci-empty-item', 'No certifications added yet.'));
  const button = document.querySelector('[data-toggle="certifications"]');
  button.dataset.collapsedLabel = 'View Details ›';
  toggleRows(button, nodes, 2);
  return nodes.length;
}

function renderAdditionalDetails(profile) {
  const target = document.querySelector('[data-additional-list]');
  const entries = [];
  const careerStatus = profile.careerSkills?.careerStatus;
  if (careerStatus) entries.push(`Career Status: ${careerStatus}`);
  const achievements = profile.additionalInformation?.achievements;
  if (achievements) entries.push(`Achievements: ${achievements}`);
  entries.forEach((text) => target.append(element('li', '', text)));
  if (!entries.length) target.append(element('li', 'ci-empty-item', 'No additional details added yet.'));
}

function renderProfile(profile) {
  const basic = profile.basicInformation || {};
  const career = profile.careerSkills || {};
  const additional = profile.additionalInformation || {};
  const firstName = String(basic.firstName || '').trim();
  const fullName = [firstName, basic.lastName].map((part) => String(part || '').trim()).filter(Boolean).join(' ');
  const degree = basic.degree === 'other' ? basic.degreeOther : basic.degree;
  const field = basic.fieldOfStudy === 'other' ? basic.fieldOfStudyOther : basic.fieldOfStudy;
  const educationSummary = [degree, field].filter(Boolean).join(' in ');
  const initial = firstName ? firstName.charAt(0).toLocaleUpperCase() : '?';
  document.querySelector('[data-user-avatar]').textContent = initial;
  document.querySelector('[data-user-avatar]').setAttribute('aria-label', fullName ? `${fullName} profile` : 'Profile avatar');
  document.querySelector('[data-overview-avatar]').textContent = initial;
  setText('[data-full-name]', fullName || 'Profile summary');
  setText('[data-degree-summary]', educationSummary);

  const educationCount = renderEducation(profile);
  const experienceCount = renderExperiences(career);
  const skillCount = renderSkills(career.technicalSkills);
  const projectCount = renderProjects(additional.projects);
  renderCertifications(additional.certifications);
  renderAdditionalDetails(profile);

  setCount('[data-education-count]', educationCount, 'degree');
  const internshipCount = Array.isArray(career.internshipExperience) ? career.internshipExperience.length : 0;
  const professionalCount = Array.isArray(career.professionalExperience) ? career.professionalExperience.length : 0;
  const experienceSummary = !experienceCount
    ? '0 entries'
    : !professionalCount
      ? `${internshipCount} ${internshipCount === 1 ? 'internship' : 'internships'}`
      : !internshipCount
        ? `${professionalCount} ${professionalCount === 1 ? 'experience' : 'experiences'}`
        : `${experienceCount} total`;
  setText('[data-experience-count]', experienceSummary);
  setCount('[data-skill-count]', skillCount, 'skill');
  setCount('[data-project-count]', projectCount, 'project');
  loadingPanel.hidden = true;
  emptyPanel.hidden = true;
  errorPanel.hidden = true;
  dashboard.hidden = false;
}

async function loadProfile() {
  loadingPanel.hidden = false;
  emptyPanel.hidden = true;
  errorPanel.hidden = true;
  dashboard.hidden = true;
  try {
    const profile = await apiGet('/api/profiles/me');
    renderProfile(profile);
  } catch (error) {
    loadingPanel.hidden = true;
    if (error.status === 404) {
      emptyPanel.hidden = false;
      return;
    }
    if (error.status === 401) {
      window.location.assign('login.html');
      return;
    }
    errorMessage.textContent = 'Please check your connection and try again.';
    errorPanel.hidden = false;
  }
}

document.querySelector('[data-retry]').addEventListener('click', loadProfile);
function setActiveStep(stepNumber) {
  stepItems.forEach((step) => {
    const number = Number(step.dataset.step);
    const current = number === stepNumber;
    step.classList.toggle('is-current', current);
    step.classList.toggle('is-complete', number < stepNumber);
    if (current) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
}

function showCareerPreferences() {
  profileSummaryScreen.hidden = true;
  recommendationsScreen.hidden = true;
  analysisScreen.hidden = true;
  preferencesScreen.hidden = false;
  setActiveStep(2);
  window.scrollTo(0, 0);
  preferencesScreen.querySelector('h1').focus({ preventScroll: true });
}

function showCareerRecommendations() {
  const preferences = window.careerPreferences;
  const hasCompletePreferences = preferences
    && preferenceFields.every((field) => typeof preferences[field] === 'string' && preferences[field].trim());

  if (!hasCompletePreferences) {
    preferencesScreen.hidden = false;
    recommendationsScreen.hidden = true;
    analysisScreen.hidden = true;
    profileSummaryScreen.hidden = true;
    preferencesValidation.textContent = 'Your preferences are not available in this browser session. Please complete all four questions again.';
    preferencesValidation.hidden = false;
    setActiveStep(2);
    window.scrollTo(0, 0);
    preferencesScreen.querySelector('h1').focus({ preventScroll: true });
    return;
  }

  profileSummaryScreen.hidden = true;
  preferencesScreen.hidden = true;
  analysisScreen.hidden = true;
  recommendationsScreen.hidden = false;
  setActiveStep(3);
  window.scrollTo(0, 0);
  recommendationsScreen.querySelector('h1').focus({ preventScroll: true });
  requestRecommendations(preferences);
}

function returnToCareerRecommendations() {
  if (!window.careerPreferences) {
    showCareerPreferences();
    return;
  }
  profileSummaryScreen.hidden = true;
  preferencesScreen.hidden = true;
  analysisScreen.hidden = true;
  recommendationsScreen.hidden = false;
  setActiveStep(3);
  window.scrollTo(0, 0);
  recommendationsScreen.querySelector('h1').focus({ preventScroll: true });
}

document.querySelector('[data-continue]').addEventListener('click', showCareerPreferences);

preferencesForm.querySelectorAll('select').forEach((select) => {
  select.addEventListener('change', () => {
    careerPreferencesState[select.name] = select.value;
    select.classList.remove('is-invalid');
    select.setAttribute('aria-invalid', 'false');
      if (!preferencesForm.querySelector('.ci-select.is-invalid')) {
        preferencesValidation.hidden = true;
        preferencesValidation.textContent = '';
      }
    preferencesNote.hidden = true;
    window.careerPreferences = null;
  });
});

preferencesForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const selects = [...preferencesForm.querySelectorAll('select')];
  const invalid = selects.filter((select) => {
    const missing = !select.value;
    select.classList.toggle('is-invalid', missing);
    select.setAttribute('aria-invalid', String(missing));
    return missing;
  });

  if (invalid.length) {
    preferencesValidation.textContent = 'Please answer all four questions before continuing.';
    preferencesValidation.hidden = false;
    invalid[0].focus();
    return;
  }

  const answers = Object.fromEntries(selects.map((select) => [select.name, select.value]));
  Object.assign(careerPreferencesState, answers);
  window.careerPreferences = { ...answers };
  preferencesNote.textContent = 'Your preferences are sent for this recommendation request only and are not saved to your profile.';
  preferencesNote.hidden = false;
  preferencesValidation.hidden = true;
  showCareerRecommendations();
});

document.querySelectorAll('[data-return-preferences]').forEach((button) => {
  button.addEventListener('click', showCareerPreferences);
});
document.querySelector('[data-retry-recommendations]').addEventListener('click', () => {
  if (window.careerPreferences) requestRecommendations(window.careerPreferences);
});
document.querySelector('[data-continue-analysis]').addEventListener('click', () => {
  const message = document.querySelector('[data-analysis-unavailable]');
  message.textContent = 'Choose View Career Analysis on a career match above.';
  message.hidden = false;
});

document.querySelector('[data-back-recommendations]').addEventListener('click', returnToCareerRecommendations);
analysisRetry.addEventListener('click', () => requestCareerAnalysis());

document.querySelector('[data-ai-consent-grant]').addEventListener('click', () => saveAIConsentDecision('granted'));
document.querySelector('[data-ai-consent-decline]').addEventListener('click', () => saveAIConsentDecision('declined'));
document.querySelector('[data-ai-consent-enable]').addEventListener('click', () => saveAIConsentDecision('granted'));
document.querySelector('[data-ai-consent-withdraw]').addEventListener('click', withdrawAIConsent);
aiConsentRetry.addEventListener('click', loadAIConsent);
document.querySelector('[data-open-ai-privacy-settings]').addEventListener('click', () => {
  aiPrivacyDetails.open = true;
  requestAnimationFrame(() => aiPrivacyDetails.querySelector('summary').focus({ preventScroll: true }));
});

loadAIConsent();
loadProfile();
