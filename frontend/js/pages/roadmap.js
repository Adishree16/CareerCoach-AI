import { apiGet } from '../services/api.js';

const loading = document.querySelector('[data-roadmap-loading]');
const errorPanel = document.querySelector('[data-roadmap-error]');
const invalidPanel = document.querySelector('[data-roadmap-invalid]');
const content = document.querySelector('[data-roadmap-content]');
const errorTitle = document.querySelector('[data-error-title]');
const errorMessage = document.querySelector('[data-error-message]');
const retryButton = document.querySelector('[data-roadmap-retry]');
const signInLink = document.querySelector('[data-roadmap-sign-in]');
const roleId = new URLSearchParams(window.location.search).get('role_id');
let requestInProgress = false;

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function skillId(value) {
  if (!isRecord(value)) return null;
  const id = typeof value.skill_id === 'string' ? value.skill_id : value.id;
  return typeof id === 'string' && id ? id : null;
}

function collectSkillIds(values, target) {
  if (!Array.isArray(values)) return;
  values.forEach((item) => {
    const id = skillId(item);
    if (id) target.add(id);
  });
}

function validEnrichmentStatements(entries, maxEntries, allowedSkills, allowedCategories, allowedProjectIndexes) {
  if (!Array.isArray(entries) || entries.length > maxEntries) return null;
  if (!entries.length) return [];
  const validated = [];
  for (const entry of entries) {
    if (!isRecord(entry) || typeof entry.text !== 'string'
      || entry.text.trim().length < 8 || entry.text.length > 300
      || !Array.isArray(entry.skill_ids) || !Array.isArray(entry.evidence_categories)
      || !Array.isArray(entry.project_indexes)) return null;
    const skills = entry.skill_ids;
    const categories = entry.evidence_categories;
    const indexes = entry.project_indexes;
    if (new Set(skills).size !== skills.length
      || skills.some((id) => typeof id !== 'string' || !allowedSkills.has(id))
      || new Set(categories).size !== categories.length
      || categories.some((category) => typeof category !== 'string' || !allowedCategories.has(category))
      || new Set(indexes).size !== indexes.length
      || indexes.some((index) => !Number.isInteger(index) || !allowedProjectIndexes.has(index))
      || (!skills.length && !categories.length && !indexes.length)) return null;
    validated.push({
      text: entry.text.trim(),
      skill_ids: [...skills],
      evidence_categories: [...categories],
      project_indexes: [...indexes],
    });
  }
  return validated;
}

function validatedRoadmapEnrichment(data) {
  const enrichment = data?.ai_enrichment;
  if (!isRecord(enrichment) || enrichment.available !== true || !isRecord(enrichment.content)) return null;
  const content = enrichment.content;
  const existing = data.your_existing_skills;
  const skills = data.skills_to_explore;
  const matchedIds = new Set();
  collectSkillIds(existing.demonstrated_technical_skills, matchedIds);
  (Array.isArray(existing.structured_project_and_experience) ? existing.structured_project_and_experience : [])
    .forEach((entry) => collectSkillIds(entry?.relevant_skills, matchedIds));
  const gapIds = new Set();
  (Array.isArray(skills.individual_requirements) ? skills.individual_requirements : [])
    .forEach((group) => collectSkillIds(group?.skills, gapIds));
  (Array.isArray(skills.complementary_requirements) ? skills.complementary_requirements : [])
    .forEach((group) => collectSkillIds(group?.skills_to_explore, gapIds));
  (Array.isArray(skills.alternative_requirements) ? skills.alternative_requirements : [])
    .forEach((group) => collectSkillIds(group?.options, gapIds));
  (Array.isArray(skills.compatible_stack_requirements) ? skills.compatible_stack_requirements : [])
    .forEach((group) => (Array.isArray(group?.options) ? group.options : [])
      .forEach((option) => collectSkillIds(option?.skills_to_explore, gapIds)));
  const evidenceCategories = new Set(
    (Array.isArray(existing.structured_project_and_experience) ? existing.structured_project_and_experience : [])
      .map((item) => item?.source_category)
      .filter((category) => ['projects', 'internships', 'professional_experience'].includes(category)),
  );
  const ideas = curatedProjectEntries(data.practice_projects.ideas);
  const projectIndexes = new Set(ideas.map((item) => item.index).filter((index) => index < 12));
  const allowedSkills = new Set([...matchedIds, ...gapIds]);
  const learning = validEnrichmentStatements(content.learning_guidance, 8, allowedSkills, evidenceCategories, projectIndexes);
  const practice = validEnrichmentStatements(content.practice_approaches, 5, allowedSkills, evidenceCategories, projectIndexes);
  const curatedRoleIds = new Set(
    (Array.isArray(data.career_growth.related_roles) ? data.career_growth.related_roles : [])
      .map((role) => role?.role_id)
      .filter((id) => typeof id === 'string'),
  );
  const rawCareerGrowth = content.career_growth;
  let careerGrowth = null;
  if (Array.isArray(rawCareerGrowth) && rawCareerGrowth.length <= 5) {
    const validated = rawCareerGrowth.every((statement) => (
      isRecord(statement)
        && typeof statement.text === 'string'
        && statement.text.trim().length >= 8
        && statement.text.length <= 300
        && Array.isArray(statement.related_role_ids)
        && statement.related_role_ids.length >= 1
        && statement.related_role_ids.length <= 8
        && new Set(statement.related_role_ids).size === statement.related_role_ids.length
        && statement.related_role_ids.every((id) => typeof id === 'string' && curatedRoleIds.has(id))
    ));
    if (validated) {
      careerGrowth = rawCareerGrowth.map((statement) => ({
        text: statement.text.trim(),
        related_role_ids: [...statement.related_role_ids],
      }));
    }
  }
  return { learning, practice, careerGrowth, gapIds };
}

function appendAIStatements(parent, title, statements) {
  if (!Array.isArray(statements) || !statements.length) return false;
  const section = make('div', 'roadmap-subsection');
  section.setAttribute('data-ai-generated-guidance', 'true');
  section.append(make('h3', '', title));
  const listNode = make('ul', 'roadmap-list');
  statements.forEach((statement) => listNode.append(make('li', '', statement.text)));
  section.append(listNode);
  parent.append(section);
  return true;
}

function renderPracticeProjects(ideas, approaches) {
  const target = document.querySelector('[data-practice-projects]');
  target.replaceChildren();
  const byIndex = new Map();
  (Array.isArray(approaches) ? approaches : []).forEach((statement) => {
    statement.project_indexes.forEach((index) => {
      if (!byIndex.has(index)) byIndex.set(index, []);
      byIndex.get(index).push(statement.text);
    });
  });
  curatedProjectEntries(ideas).forEach(({ name, index }) => {
    const item = make('li', 'roadmap-project-item');
    item.append(make('strong', '', name));
    (byIndex.get(index) || []).forEach((explanation) => item.append(make('p', 'roadmap-muted', explanation)));
    target.append(item);
  });
  const hasItems = target.childElementCount > 0;
  document.querySelector('[data-practice-projects-empty]').hidden = hasItems;
  return hasItems;
}

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function list(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === 'string' && item.trim()) : [];
}

function curatedProjectEntries(value) {
  return Array.isArray(value)
    ? value.flatMap((name, index) => typeof name === 'string' && name.trim() ? [{ index, name: name.trim() }] : [])
    : [];
}

function make(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function showList(selector, values, emptySelector) {
  const target = document.querySelector(selector);
  const empty = emptySelector ? document.querySelector(emptySelector) : null;
  if (!target) return;
  target.replaceChildren();
  const entries = list(values);
  entries.forEach((value) => target.append(make('li', '', value)));
  if (empty) empty.hidden = entries.length > 0;
  target.hidden = entries.length === 0;
}

function showError(status) {
  loading.hidden = true;
  content.hidden = true;
  invalidPanel.hidden = true;
  errorPanel.hidden = false;
  signInLink.hidden = status !== 401;
  if (status === 401) {
    errorTitle.textContent = 'Please sign in to view your roadmap';
    errorMessage.textContent = 'Your sign-in session may have expired. Sign in, then return to the selected career roadmap.';
  } else if (status === 404) {
    errorTitle.textContent = 'This roadmap is not available';
    errorMessage.textContent = 'We could not find the saved profile or selected career role. Return to Career Intelligence and try again.';
  } else if (status === 422) {
    errorTitle.textContent = 'The selected career role could not be used';
    errorMessage.textContent = 'Return to Career Intelligence and choose a role from the available career list.';
  } else {
    errorTitle.textContent = 'We couldn’t load your roadmap';
    errorMessage.textContent = 'The roadmap service is unavailable or returned an incomplete response. You can retry without losing your selected role.';
  }
}

function validResponse(data) {
  return isRecord(data)
    && isRecord(data.career_overview)
    && data.career_overview.role_id === roleId
    && typeof data.career_overview.name === 'string'
    && typeof data.career_overview.domain === 'string'
    && typeof data.career_overview.description === 'string'
    && Array.isArray(data.career_overview.main_responsibilities)
    && isRecord(data.your_existing_skills)
    && Array.isArray(data.your_existing_skills.demonstrated_technical_skills)
    && Array.isArray(data.your_existing_skills.structured_project_and_experience)
    && Array.isArray(data.your_existing_skills.eligible_certifications)
    && isRecord(data.skills_to_explore)
    && Array.isArray(data.skills_to_explore.individual_requirements)
    && Array.isArray(data.skills_to_explore.complementary_requirements)
    && Array.isArray(data.skills_to_explore.alternative_requirements)
    && Array.isArray(data.skills_to_explore.compatible_stack_requirements)
    && isRecord(data.learning_guidance)
    && Array.isArray(data.learning_guidance.catalogue_supported)
    && Array.isArray(data.learning_guidance.general_guidance)
    && isRecord(data.practice_projects)
    && Array.isArray(data.practice_projects.ideas)
    && isRecord(data.career_growth)
    && Array.isArray(data.career_growth.related_roles)
    && Array.isArray(data.career_growth.progression_paths);
}

function renderSkill(skill) {
  if (!isRecord(skill)) return null;
  const name = text(skill.name);
  if (!name) return null;
  const item = make('li', 'roadmap-skill-item');
  const heading = make('strong', '', name);
  const meta = [text(skill.category_name), text(skill.importance)].filter(Boolean).join(' · ');
  item.append(heading);
  if (meta) item.append(make('span', 'roadmap-skill-meta', meta));
  if (text(skill.why_relevant)) item.append(make('p', 'roadmap-skill-relevance', text(skill.why_relevant)));
  return item;
}

function appendSkills(target, skills) {
  const safeSkills = Array.isArray(skills) ? skills.map(renderSkill).filter(Boolean) : [];
  safeSkills.forEach((skill) => target.append(skill));
  return safeSkills.length;
}

function renderEvidence(evidence) {
  const target = document.querySelector('[data-structured-evidence]');
  target.replaceChildren();
  const records = Array.isArray(evidence) ? evidence.filter(isRecord) : [];
  records.forEach((record) => {
    const names = Array.isArray(record.relevant_skills)
      ? record.relevant_skills.map((skill) => text(skill?.name)).filter(Boolean)
      : [];
    if (!names.length) return;
    const article = make('article', 'roadmap-evidence-item');
    article.append(make('h4', '', text(record.source_label) || 'Profile evidence'));
    const listNode = make('ul', 'roadmap-compact-list');
    names.forEach((name) => listNode.append(make('li', '', name)));
    article.append(listNode);
    target.append(article);
  });
  const hasItems = target.childElementCount > 0;
  target.hidden = !hasItems;
  document.querySelector('[data-structured-evidence-empty]').hidden = hasItems;
}

function renderCertifications(certifications) {
  const section = document.querySelector('[data-certification-section]');
  const target = document.querySelector('[data-certifications]');
  target.replaceChildren();
  const entries = Array.isArray(certifications) ? certifications.filter(isRecord) : [];
  entries.forEach((credential) => {
    const name = text(credential.name);
    if (!name) return;
    const article = make('article', 'roadmap-evidence-item');
    article.append(make('h4', '', name));
    const organization = text(credential.issuing_organization);
    if (organization) article.append(make('p', 'roadmap-muted', organization));
    const skills = make('ul', 'roadmap-compact-list');
    const count = appendSkills(skills, credential.relevant_skills);
    if (count) article.append(skills);
    target.append(article);
  });
  section.hidden = target.childElementCount === 0;
}

function renderIndividualRequirements(groups) {
  const target = document.querySelector('[data-individual-skills]');
  target.replaceChildren();
  (Array.isArray(groups) ? groups : []).forEach((group) => {
    if (!isRecord(group)) return;
    const skills = make('ul', 'roadmap-skill-list');
    const count = appendSkills(skills, group.skills);
    if (!count) return;
    const section = make('section', 'roadmap-skill-group');
    section.append(make('h4', '', text(group.category_name) || 'Role skills'));
    if (text(group.importance)) section.append(make('p', 'roadmap-group-meta', `${text(group.importance)} importance`));
    section.append(skills);
    target.append(section);
  });
  const hasItems = target.childElementCount > 0;
  document.querySelector('[data-individual-section]').hidden = !hasItems;
  return hasItems;
}

function renderAlternatives(groups) {
  const target = document.querySelector('[data-alternative-requirements]');
  target.replaceChildren();
  (Array.isArray(groups) ? groups : []).forEach((group) => {
    if (!isRecord(group)) return;
    const article = make('article', 'roadmap-requirement-card');
    article.append(make('h4', '', text(group.name) || 'Alternative options'));
    const minimum = Number.isInteger(group.minimum_options_to_learn) ? group.minimum_options_to_learn : null;
    const demonstrated = Number.isInteger(group.options_already_demonstrated) ? group.options_already_demonstrated : 0;
    const additional = Number.isInteger(group.additional_options_to_explore) ? group.additional_options_to_explore : null;
    if (minimum !== null) {
      const countText = additional === null
        ? `At least ${minimum} option${minimum === 1 ? '' : 's'} from this group are required.`
        : `${demonstrated} of at least ${minimum} option${minimum === 1 ? '' : 's'} are already represented in your profile; explore ${additional} more.`;
      article.append(make('p', 'roadmap-group-meta', countText));
    }
    const skills = make('ul', 'roadmap-skill-list');
    if (appendSkills(skills, group.options)) article.append(skills);
    if (article.querySelector('.roadmap-skill-item')) target.append(article);
  });
  const hasItems = target.childElementCount > 0;
  document.querySelector('[data-alternatives-section]').hidden = !hasItems;
  return hasItems;
}

function renderComplementary(groups) {
  const target = document.querySelector('[data-complementary-requirements]');
  target.replaceChildren();
  (Array.isArray(groups) ? groups : []).forEach((group) => {
    if (!isRecord(group)) return;
    const skills = make('ul', 'roadmap-skill-list');
    if (!appendSkills(skills, group.skills_to_explore)) return;
    const article = make('article', 'roadmap-requirement-card');
    article.append(make('h4', '', text(group.name) || 'Complementary skills'));
    article.append(skills);
    target.append(article);
  });
  const hasItems = target.childElementCount > 0;
  document.querySelector('[data-complementary-section]').hidden = !hasItems;
  return hasItems;
}

function renderStacks(groups) {
  const target = document.querySelector('[data-compatible-stacks]');
  target.replaceChildren();
  (Array.isArray(groups) ? groups : []).forEach((group) => {
    if (!isRecord(group)) return;
    const requirement = make('article', 'roadmap-requirement-card');
    const minimum = Number.isInteger(group.minimum_options_to_learn) ? group.minimum_options_to_learn : null;
    const demonstratedCount = Number.isInteger(group.options_already_demonstrated) ? group.options_already_demonstrated : 0;
    const additional = Number.isInteger(group.additional_options_to_explore) ? group.additional_options_to_explore : null;
    if (minimum !== null) {
      const countText = additional === null
        ? `At least ${minimum} compatible stack option${minimum === 1 ? '' : 's'} are required. Keep each stack as a complete combination.`
        : `${demonstratedCount} of at least ${minimum} compatible stack option${minimum === 1 ? '' : 's'} are already represented; explore ${additional} more as complete combinations.`;
      requirement.append(make('p', 'roadmap-group-meta', countText));
    }
    const options = Array.isArray(group.options) ? group.options : [];
    options.forEach((option) => {
      if (!isRecord(option)) return;
      const optionCard = make('section', 'roadmap-stack-option');
      optionCard.append(make('h4', '', text(option.name) || 'Compatible stack'));
      const demonstrated = Array.isArray(option.skills_already_demonstrated) ? option.skills_already_demonstrated : [];
      const toExplore = Array.isArray(option.skills_to_explore) ? option.skills_to_explore : [];
      if (demonstrated.length) {
        const listNode = make('ul', 'roadmap-compact-list');
        demonstrated.forEach((skill) => {
          if (text(skill?.name)) listNode.append(make('li', '', `${text(skill.name)} · shown in your profile`));
        });
        if (listNode.childElementCount) {
          optionCard.append(make('p', 'roadmap-group-meta', 'Already shown in your profile'));
          optionCard.append(listNode);
        }
      }
      if (toExplore.length) {
        const listNode = make('ul', 'roadmap-skill-list');
        appendSkills(listNode, toExplore);
        if (listNode.childElementCount) {
          optionCard.append(make('p', 'roadmap-group-meta', 'Skills to explore in this option'));
          optionCard.append(listNode);
        }
      }
      if (optionCard.childElementCount > 1) requirement.append(optionCard);
    });
    if (requirement.querySelector('.roadmap-stack-option')) target.append(requirement);
  });
  const hasItems = target.childElementCount > 0;
  document.querySelector('[data-stacks-section]').hidden = !hasItems;
  return hasItems;
}

function renderResponse(data) {
  document.querySelectorAll('[data-ai-generated-guidance]').forEach((node) => node.remove());
  const overview = data.career_overview;
  const existing = data.your_existing_skills;
  const toExplore = data.skills_to_explore;
  const guidance = data.learning_guidance;
  const projects = data.practice_projects;
  const growth = data.career_growth;
  const ai = validatedRoadmapEnrichment(data);

  document.querySelector('[data-role-name]').textContent = text(overview.name) || 'Career role';
  document.querySelector('[data-role-domain]').textContent = text(overview.domain) || 'Career domain not provided';
  document.querySelector('[data-role-description]').textContent = text(overview.description) || 'A role description is not available yet.';
  showList('[data-responsibilities]', overview.main_responsibilities, '[data-responsibilities-empty]');

  const demonstrated = document.querySelector('[data-demonstrated-skills]');
  demonstrated.replaceChildren();
  const skillCount = appendSkills(demonstrated, existing.demonstrated_technical_skills);
  demonstrated.hidden = skillCount === 0;
  document.querySelector('[data-demonstrated-skills-empty]').hidden = skillCount > 0;
  renderEvidence(existing.structured_project_and_experience);
  renderCertifications(existing.eligible_certifications);
  document.querySelector('[data-existing-skills-note]').textContent = text(existing.note);

  const hasIndividual = renderIndividualRequirements(toExplore.individual_requirements);
  const hasAlternatives = renderAlternatives(toExplore.alternative_requirements);
  const hasComplementary = renderComplementary(toExplore.complementary_requirements);
  const hasStacks = renderStacks(toExplore.compatible_stack_requirements);
  const skillGuidance = ai?.learning?.filter((statement) => (
    statement.skill_ids.length > 0
      && statement.skill_ids.every((id) => ai.gapIds.has(id))
      && statement.evidence_categories.length === 0
      && statement.project_indexes.length === 0
  )) || [];
  const skillGuidanceAdded = appendAIStatements(
    document.querySelector('[aria-labelledby="explore-skills-title"]'),
    'Personalized topics to explore',
    skillGuidance,
  );
  document.querySelector('[data-skills-empty]').hidden = hasIndividual || hasAlternatives || hasComplementary || hasStacks || skillGuidanceAdded;
  document.querySelector('[data-skills-note]').textContent = text(toExplore.note);

  showList('[data-catalogue-guidance]', guidance.catalogue_supported, '[data-catalogue-guidance-empty]');
  showList('[data-general-guidance]', guidance.general_guidance, '[data-general-guidance-empty]');
  const generalAIguidance = (ai?.learning || []).filter((statement) => !skillGuidance.includes(statement));
  if (generalAIguidance.length) {
    const grid = document.querySelector('.roadmap-guidance-grid');
    const panel = make('div', 'roadmap-ai-guidance');
    panel.setAttribute('data-ai-generated-guidance', 'true');
    panel.append(make('h3', '', 'Personalized guidance'));
    const guidanceList = make('ul', 'roadmap-list');
    generalAIguidance.forEach((statement) => guidanceList.append(make('li', '', statement.text)));
    panel.append(guidanceList);
    grid.append(panel);
  }

  renderPracticeProjects(projects.ideas, ai?.practice);
  document.querySelector('[data-projects-note]').textContent = text(projects.note);

  const related = document.querySelector('[data-related-roles]');
  related.replaceChildren();
  const relatedRoles = Array.isArray(growth.related_roles) ? growth.related_roles : [];
  relatedRoles.forEach((role) => {
    if (text(role?.name)) related.append(make('li', '', text(role.name)));
  });
  related.hidden = related.childElementCount === 0;
  document.querySelector('[data-related-roles-empty]').hidden = related.childElementCount > 0;

  const paths = document.querySelector('[data-progression-paths]');
  paths.replaceChildren();
  const progressionPaths = Array.isArray(growth.progression_paths) ? growth.progression_paths : [];
  progressionPaths.forEach((path) => {
    const names = list(path);
    if (!names.length) return;
    const item = make('li', 'roadmap-path');
    names.forEach((name, index) => {
      if (index) item.append(make('span', 'roadmap-path-arrow', '→'));
      item.append(make('span', 'roadmap-path-role', name));
    });
    paths.append(item);
  });
  paths.hidden = paths.childElementCount === 0;
  document.querySelector('[data-progression-paths-empty]').hidden = paths.childElementCount > 0;
  if (ai?.careerGrowth?.length) {
    const growthSection = document.querySelector('.roadmap-growth');
    const panel = make('div', 'roadmap-subsection roadmap-ai-guidance');
    panel.setAttribute('data-ai-generated-guidance', 'true');
    panel.append(make('h3', '', 'Personalized career growth ideas'));
    const statements = make('ul', 'roadmap-list');
    ai.careerGrowth.forEach((statement) => statements.append(make('li', '', statement.text)));
    panel.append(statements);
    growthSection.append(panel);
  }
  document.querySelector('[data-growth-note]').textContent = text(growth.note);

  document.title = `${text(overview.name) || 'Career'} Roadmap | CareerCoach AI`;
  content.hidden = false;
  content.setAttribute('aria-busy', 'false');
}

async function loadRoadmap() {
  if (requestInProgress) return;
  requestInProgress = true;
  retryButton.disabled = true;
  errorPanel.hidden = true;
  invalidPanel.hidden = true;
  content.hidden = true;
  loading.hidden = false;
  if (!roleId || !/^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(roleId)) {
    loading.hidden = true;
    invalidPanel.hidden = false;
    requestInProgress = false;
    retryButton.disabled = false;
    return;
  }

  content.setAttribute('aria-busy', 'true');
  try {
    const data = await apiGet(`/api/career-roadmaps/${encodeURIComponent(roleId)}`);
    if (!validResponse(data)) {
      showError(0);
      return;
    }
    renderResponse(data);
    loading.hidden = true;
  } catch (error) {
    showError(error?.status);
  } finally {
    requestInProgress = false;
    retryButton.disabled = false;
  }
}

retryButton.addEventListener('click', loadRoadmap);
loadRoadmap();
