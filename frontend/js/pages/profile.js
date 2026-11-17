import { apiGet, apiPut } from '../services/api.js';

const profileForm = document.querySelector("#profile-form");

if (profileForm) {
  const educationSelect = document.querySelector("#education-level");
  const degreeSelect = document.querySelector("#degree");
  const fieldSelect = document.querySelector("#field-of-study");
  const statusMessage = document.querySelector("#form-status");
  const careerStatusMessage = document.querySelector("#career-form-status");
  const basicSection = document.querySelector("#basic-section");
  const careerSection = document.querySelector("#career-section");
  const additionalSection = document.querySelector("#additional-section");
  const additionalStatusMessage = document.querySelector("#additional-form-status");
  const profileCompletion = document.querySelector("#profile-completion");
  const projectList = document.querySelector("#project-list");
  const projectTemplate = document.querySelector("#project-template");
  const certificationList = document.querySelector("#certification-list");
  const certificationTemplate = document.querySelector("#certification-template");
  const achievements = document.querySelector("#achievements");
  const progressCaption = document.querySelector(".progress-caption");
  const progressSteps = [...document.querySelectorAll(".progress-step")];
  const skillInput = document.querySelector("#skill-input");
  const skillList = document.querySelector("#skill-list");
  const skillError = document.querySelector("#skill-error");
  const internshipSection = document.querySelector("#internship-experience");
  const internshipList = document.querySelector("#internship-list");
  const internshipTemplate = document.querySelector("#internship-template");
  const professionalSection = document.querySelector("#professional-experience");
  const experienceList = document.querySelector("#experience-list");
  const experienceTemplate = document.querySelector("#experience-template");
  const careerRadios = [...document.querySelectorAll('input[name="careerStatus"]')];
  let nextExperienceIndex = 1;
  let nextInternshipIndex = 1;
  let nextProjectIndex = 1;
  let nextCertificationIndex = 1;
  let profileLoadPromise = null;
  let isSavingProfile = false;

  const educationLevels = {
    secondary: "Secondary School",
    "higher-secondary": "Higher Secondary / Pre-University",
    diploma: "Diploma",
    undergraduate: "Undergraduate",
    postgraduate: "Postgraduate",
    doctorate: "Doctorate",
    other: "Other",
  };

  const degreesByLevel = {
    secondary: ["Secondary School Certificate"],
    "higher-secondary": ["Higher Secondary Certificate", "Pre-University Certificate", "Senior Secondary Certificate"],
    diploma: ["Diploma in Engineering", "Diploma in Computer Applications", "Diploma in Business", "Diploma in Design", "Diploma in Healthcare"],
    undergraduate: ["B.E. / B.Tech", "B.Sc", "BCA", "B.Com", "BBA", "B.A", "B.Arch", "B.Des", "MBBS", "BDS", "B.Pharm", "BPT", "B.Sc Nursing", "LLB", "B.Ed", "BSW"],
    postgraduate: ["M.E. / M.Tech", "M.Sc", "MCA", "M.Com", "MBA", "M.A", "M.Arch", "M.Des", "MD", "MS", "M.Pharm", "MPT", "M.Sc Nursing", "LLM", "M.Ed", "MSW"],
    doctorate: ["Ph.D.", "Doctor of Science (D.Sc.)", "Doctor of Letters (D.Litt.)", "Professional Doctorate"],
  };

  const allDegrees = [...new Set(Object.values(degreesByLevel).flat())];
  const branches = {
    computing: ["Computer Science", "Information Technology", "Software Engineering", "Data Science", "Artificial Intelligence", "Cybersecurity", "Information Systems"],
    engineering: ["Mechanical Engineering", "Civil Engineering", "Electrical Engineering", "Electronics & Communication", "Chemical Engineering", "Automobile Engineering", "Aerospace Engineering", "Industrial Engineering", "Mechatronics", "Biotechnology"],
    science: ["Physics", "Chemistry", "Mathematics", "Biology", "Statistics", "Environmental Science", "Biotechnology", "Microbiology"],
    commerce: ["Accounting", "Finance", "Marketing", "Human Resources", "Business Administration", "Economics", "International Business", "Supply Chain Management"],
    arts: ["English", "History", "Psychology", "Sociology", "Political Science", "Economics", "Journalism", "Languages", "Social Work"],
    design: ["Architecture", "Interior Design", "Graphic Design", "Product Design", "Fashion Design", "User Experience Design", "Urban Planning"],
    health: ["Medicine", "Dentistry", "Pharmacy", "Nursing", "Physiotherapy", "Public Health", "Medical Laboratory Science", "Nutrition"],
    lawEducation: ["Law", "Education", "Curriculum & Instruction", "Educational Leadership"],
    streams: ["Science", "Commerce", "Arts / Humanities", "Vocational Studies"],
  };

  const degreeGroups = {
    "B.E. / B.Tech": ["engineering", "computing"], "M.E. / M.Tech": ["engineering", "computing"],
    "B.Sc": ["science"], "M.Sc": ["science"], BCA: ["computing"], MCA: ["computing"],
    "B.Com": ["commerce"], "M.Com": ["commerce"], BBA: ["commerce"], MBA: ["commerce"],
    "B.A": ["arts"], "M.A": ["arts"], BSW: ["arts"], MSW: ["arts"],
    "B.Arch": ["design"], "M.Arch": ["design"], "B.Des": ["design"], "M.Des": ["design"],
    MBBS: ["health"], MD: ["health"], MS: ["health"], BDS: ["health"], "B.Pharm": ["health"], "M.Pharm": ["health"],
    BPT: ["health"], MPT: ["health"], "B.Sc Nursing": ["health"], "M.Sc Nursing": ["health"],
    LLB: ["lawEducation"], LLM: ["lawEducation"], "B.Ed": ["lawEducation"], "M.Ed": ["lawEducation"],
    "Diploma in Engineering": ["engineering"], "Diploma in Computer Applications": ["computing"],
    "Diploma in Business": ["commerce"], "Diploma in Design": ["design"], "Diploma in Healthcare": ["health"],
    "Secondary School Certificate": ["streams"], "Higher Secondary Certificate": ["streams"],
    "Pre-University Certificate": ["streams"], "Senior Secondary Certificate": ["streams"],
  };

  const groupOrder = ["computing", "engineering", "science", "commerce", "arts", "design", "health", "lawEducation", "streams"];
  const otherInputs = [
    { select: educationSelect, wrap: document.querySelector("#education-level-other-wrap"), input: document.querySelector("#education-level-other") },
    { select: degreeSelect, wrap: document.querySelector("#degree-other-wrap"), input: document.querySelector("#degree-other") },
    { select: fieldSelect, wrap: document.querySelector("#field-of-study-other-wrap"), input: document.querySelector("#field-of-study-other") },
  ];

  // Keep the current profile snapshot in page memory while it is edited.
  const profileSetupState = {
    basicInformation: null,
    careerSkills: {
      careerStatus: "",
      technicalSkills: [],
      internshipExperience: [],
      professionalExperience: [],
    },
    additionalInformation: {
      projects: [],
      certifications: [],
      achievements: "",
    },
    profileStageCompleted: false,
  };
  window.profileSetupState = profileSetupState;

  function clearFieldError(field) {
    const error = document.querySelector(`[data-error-for="${field.id}"]`);
    if (error) error.textContent = "";
    field.removeAttribute("aria-invalid");
  }

  function resetSelect(select, placeholder) {
    select.replaceChildren(new Option(placeholder, "", true, true));
    select.options[0].disabled = true;
  }

  function fillSelect(select, placeholder, items) {
    resetSelect(select, placeholder);
    items.forEach((item) => select.add(new Option(item, item)));
    select.add(new Option("Other", "other"));
  }

  function showOtherInput(config, visible) {
    config.wrap.hidden = !visible;
    config.input.disabled = !visible;
    config.input.required = visible;
    if (!visible) config.input.value = "";
    clearFieldError(config.input);
  }

  function getBranchOptions(degree) {
    if (degree === "other" || !degreeGroups[degree]) return groupOrder.flatMap((group) => branches[group]);
    const allowed = new Set(degreeGroups[degree]);
    return groupOrder.filter((group) => allowed.has(group)).flatMap((group) => branches[group]);
  }

  function resetDegreeAndField() {
    resetSelect(degreeSelect, "Select your degree");
    degreeSelect.disabled = true;
    resetSelect(fieldSelect, "Select your field of study or branch");
    fieldSelect.disabled = true;
    showOtherInput(otherInputs[1], false);
    showOtherInput(otherInputs[2], false);
    clearFieldError(degreeSelect);
    clearFieldError(fieldSelect);
  }

  function clearStatus() {
    statusMessage.hidden = true;
    statusMessage.textContent = "";
    statusMessage.classList.remove("is-success", "is-error");
    if (profileSetupState.basicInformation) profileSetupState.basicInformation = collectBasicInformation();
  }

  educationSelect.addEventListener("change", () => {
    clearStatus();
    clearFieldError(educationSelect);
    showOtherInput(otherInputs[0], educationSelect.value === "other");
    resetDegreeAndField();
    if (!educationSelect.value) return;
    const degreeOptions = educationSelect.value === "other"
      ? allDegrees
      : (degreesByLevel[educationSelect.value] || []);
    fillSelect(degreeSelect, "Select your degree", degreeOptions);
    degreeSelect.disabled = false;
  });

  degreeSelect.addEventListener("change", () => {
    clearStatus();
    clearFieldError(degreeSelect);
    showOtherInput(otherInputs[1], degreeSelect.value === "other");
    resetSelect(fieldSelect, "Select your field of study or branch");
    fieldSelect.disabled = !degreeSelect.value;
    showOtherInput(otherInputs[2], false);
    clearFieldError(fieldSelect);
    if (degreeSelect.value) fillSelect(fieldSelect, "Select your field of study or branch", getBranchOptions(degreeSelect.value));
  });

  fieldSelect.addEventListener("change", () => {
    clearStatus();
    clearFieldError(fieldSelect);
    showOtherInput(otherInputs[2], fieldSelect.value === "other");
  });

  basicSection.querySelectorAll("input").forEach((input) => {
    input.addEventListener("input", () => {
      clearFieldError(input);
      clearStatus();
    });
  });

  function validateField(field, message) {
    const error = document.querySelector(`[data-error-for="${field.id}"]`);
    const invalid = field.disabled || !String(field.value || "").trim();
    field.setAttribute("aria-invalid", String(invalid));
    if (error) error.textContent = invalid ? message : "";
    return !invalid;
  }

  function validateBasicInformation() {
    clearStatus();
    const requiredFields = [
      [document.querySelector("#first-name"), "Enter your first name."],
      [document.querySelector("#last-name"), "Enter your last name."],
      [educationSelect, "Select your education level."],
      ...(educationSelect.value === "other" ? [[otherInputs[0].input, "Enter your education level."]] : []),
      [degreeSelect, "Select your degree."],
      ...(degreeSelect.value === "other" ? [[otherInputs[1].input, "Enter your degree."]] : []),
      [fieldSelect, "Select your field of study or branch."],
      ...(fieldSelect.value === "other" ? [[otherInputs[2].input, "Enter your field of study or branch."]] : []),
    ];
    const invalidFields = requiredFields.filter(([field, message]) => !validateField(field, message));
    if (invalidFields.length) {
      statusMessage.textContent = "Please complete the required fields before continuing.";
      statusMessage.className = "form-status is-error";
      statusMessage.hidden = false;
      return invalidFields[0][0];
    }
    profileSetupState.basicInformation = collectBasicInformation();
    return null;
  }

  function setProgress(sectionNumber, completed = false) {
    progressCaption.textContent = completed ? "Step 3 of 3 · Complete" : `Step ${sectionNumber} of 3`;
    progressSteps.forEach((step, index) => {
      const active = !completed && index === sectionNumber - 1;
      const isComplete = completed || index < sectionNumber - 1;
      step.classList.toggle("is-current", active);
      step.classList.toggle("is-complete", isComplete);
      step.classList.toggle("is-pending", !active && !isComplete);
      if (active) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
    });
  }

  function switchSection(sectionNumber) {
    basicSection.hidden = sectionNumber !== 1;
    careerSection.hidden = sectionNumber !== 2;
    additionalSection.hidden = sectionNumber !== 3;
    profileCompletion.hidden = true;
    setProgress(sectionNumber);
    window.scrollTo(0, 0);
    const focusTarget = sectionNumber === 1
      ? document.querySelector("#first-name")
      : document.querySelector(sectionNumber === 2 ? "#career-section-title" : "#additional-section-title");
    focusTarget.focus({ preventScroll: true });
  }

  function collectBasicInformation() {
    return {
      firstName: document.querySelector("#first-name").value.trim(),
      lastName: document.querySelector("#last-name").value.trim(),
      educationLevel: educationSelect.value,
      educationLevelOther: otherInputs[0].input.value.trim(),
      degree: degreeSelect.value,
      degreeOther: otherInputs[1].input.value.trim(),
      fieldOfStudy: fieldSelect.value,
      fieldOfStudyOther: otherInputs[2].input.value.trim(),
    };
  }

  function clearCareerStatus() {
    careerStatusMessage.hidden = true;
    careerStatusMessage.textContent = "";
    careerStatusMessage.classList.remove("is-success", "is-error");
  }

  function setFieldError(field, errorElement, message) {
    if (errorElement) errorElement.textContent = message;
    if (field) field.setAttribute("aria-invalid", String(Boolean(message)));
    return !message;
  }

  function selectedCareerStatus() {
    return careerRadios.find((radio) => radio.checked)?.value || "";
  }

  function collectCareerInformation() {
    const collectEntries = (list, selector, prefix) => [...list.querySelectorAll(selector)].map((entry) => {
      const value = (key) => entry.querySelector(`[data-${prefix}-field="${key}"]`).value.trim();
      return {
        id: entry.dataset.index,
        company: value("company"),
        role: value("role"),
        duration: value("duration"),
        responsibilities: value("responsibilities"),
        technologies: value("technologies"),
      };
    });
    return {
      careerStatus: selectedCareerStatus(),
      technicalSkills: [...profileSetupState.careerSkills.technicalSkills],
      internshipExperience: collectEntries(internshipList, "[data-internship-entry]", "internship"),
      professionalExperience: collectEntries(experienceList, "[data-experience-entry]", "experience"),
    };
  }

  function updateCareerDraft() {
    profileSetupState.careerSkills = collectCareerInformation();
  }

  function renderSkills() {
    skillList.replaceChildren();
    profileSetupState.careerSkills.technicalSkills.forEach((skill) => {
      const item = document.createElement("li");
      item.className = "skill-chip";
      const label = document.createElement("span");
      label.textContent = skill;
      const removeButton = document.createElement("button");
      removeButton.className = "skill-remove";
      removeButton.type = "button";
      removeButton.textContent = "×";
      removeButton.setAttribute("aria-label", `Remove ${skill}`);
      removeButton.dataset.removeSkill = skill;
      item.append(label, removeButton);
      skillList.append(item);
    });
  }

  function addSkill() {
    const skill = skillInput.value.trim();
    if (!skill) {
      skillError.textContent = "Enter a skill before adding it.";
      skillInput.setAttribute("aria-invalid", "true");
      skillInput.focus();
      return;
    }
    if (skill.length > 100) {
      skillError.textContent = "Skills must be 100 characters or fewer.";
      skillInput.setAttribute("aria-invalid", "true");
      skillInput.focus();
      return;
    }
    const duplicate = profileSetupState.careerSkills.technicalSkills.some((existing) => existing.toLocaleLowerCase() === skill.toLocaleLowerCase());
    if (duplicate) {
      skillError.textContent = "That skill has already been added.";
      skillInput.setAttribute("aria-invalid", "true");
      skillInput.focus();
      return;
    }
    profileSetupState.careerSkills.technicalSkills.push(skill);
    skillInput.value = "";
    skillError.textContent = "";
    skillInput.removeAttribute("aria-invalid");
    clearCareerStatus();
    renderSkills();
    updateCareerDraft();
  }

  function addCareerEntry(list, template, entrySelector, titlePrefix, index, removeDataKey, fieldPrefix, shouldFocus = true) {
    const displayNumber = list.querySelectorAll(entrySelector).length + 1;
    const fragment = template.content.cloneNode(true);
    const entry = fragment.querySelector(entrySelector);
    entry.dataset.index = String(index);
    entry.setAttribute("aria-labelledby", `${fieldPrefix}-entry-title-${index}`);
    entry.querySelector("h4").textContent = `${titlePrefix} ${displayNumber}`;
    const attributesToUpdate = ["id", "for", "aria-describedby", "aria-labelledby"];
    entry.querySelectorAll("*").forEach((element) => {
      attributesToUpdate.forEach((attribute) => {
        const value = element.getAttribute(attribute);
        if (value) element.setAttribute(attribute, value.replaceAll("__INDEX__", String(index)).replaceAll("__NUMBER__", String(displayNumber)));
      });
    });
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "secondary-button remove-experience-button";
    removeButton.textContent = "Remove";
    removeButton.setAttribute("aria-label", `Remove ${titlePrefix.toLowerCase()} ${displayNumber}`);
    removeButton.dataset[removeDataKey] = "true";
    entry.querySelector(".experience-entry-heading").append(removeButton);
    list.append(fragment);
    clearCareerStatus();
    updateCareerDraft();
    if (shouldFocus) entry.querySelector(`[data-${fieldPrefix}-field='company']`).focus();
    return entry;
  }

  function renumberCareerEntries(list, entrySelector, titlePrefix, removeSelector) {
    list.querySelectorAll(entrySelector).forEach((entry, index) => {
      const label = `${titlePrefix} ${index + 1}`;
      entry.querySelector("h4").textContent = label;
      const removeButton = entry.querySelector(removeSelector);
      if (removeButton) removeButton.setAttribute("aria-label", `Remove ${label.toLowerCase()}`);
    });
  }

  function addAdditionalEntry(list, template, entrySelector, titlePrefix, index, removeAttribute) {
    const displayNumber = list.querySelectorAll(entrySelector).length + 1;
    const fragment = template.content.cloneNode(true);
    const entry = fragment.querySelector(entrySelector);
    entry.dataset.index = String(index);
    entry.setAttribute("aria-labelledby", `${titlePrefix}-entry-title-${index}`);
    entry.querySelector("h4").textContent = `${titlePrefix} ${displayNumber}`;
    const attributesToUpdate = ["id", "for", "aria-describedby", "aria-labelledby"];
    entry.querySelectorAll("*").forEach((element) => {
      attributesToUpdate.forEach((attribute) => {
        const value = element.getAttribute(attribute);
        if (value) element.setAttribute(attribute, value.replaceAll("__INDEX__", String(index)).replaceAll("__NUMBER__", String(displayNumber)));
      });
    });
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "secondary-button remove-experience-button";
    removeButton.textContent = "Remove";
    removeButton.setAttribute("aria-label", `Remove ${titlePrefix.toLowerCase()} ${displayNumber}`);
    removeButton.dataset[removeAttribute] = "true";
    entry.querySelector(".experience-entry-heading").append(removeButton);
    list.append(fragment);
    return entry;
  }

  function renumberAdditionalEntries(list, entrySelector, titlePrefix, removeAttribute) {
    list.querySelectorAll(entrySelector).forEach((entry, index) => {
      const label = `${titlePrefix} ${index + 1}`;
      entry.querySelector("h4").textContent = label;
      const dataAttribute = `data-${removeAttribute.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
      const removeButton = entry.querySelector(`[${dataAttribute}]`);
      if (removeButton) removeButton.setAttribute("aria-label", `Remove ${label.toLowerCase()}`);
    });
  }

  function ensureSelectOption(select, value) {
    const optionValue = String(value ?? "");
    if (optionValue && ![...select.options].some((option) => option.value === optionValue)) {
      select.add(new Option(optionValue, optionValue));
    }
    select.value = optionValue;
  }

  function dispatchChange(element) {
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function clearEntryRows(list, selector) {
    list.querySelectorAll(selector).forEach((entry) => entry.remove());
  }

  function restoreExperienceEntries(list, template, selector, titlePrefix, fieldPrefix, removeDataKey, records) {
    clearEntryRows(list, selector);
    const entries = Array.isArray(records) ? records : [];
    const rows = entries.length ? entries : [{}];
    let index = 0;
    rows.forEach((record) => {
      const entry = addCareerEntry(
        list,
        template,
        selector,
        titlePrefix,
        index++,
        removeDataKey,
        fieldPrefix,
        false,
      );
      ["company", "role", "duration", "responsibilities", "technologies"].forEach((field) => {
        const input = entry.querySelector(`[data-${fieldPrefix}-field="${field}"]`);
        input.value = String(record[field] ?? "");
      });
    });
    if (fieldPrefix === "internship") nextInternshipIndex = index;
    else nextExperienceIndex = index;
  }

  function restoreAdditionalEntries(list, template, selector, titlePrefix, fieldAttribute, fields, removeAttribute, records) {
    clearEntryRows(list, selector);
    const entries = Array.isArray(records) ? records : [];
    const rows = entries.length ? entries : [{}];
    let index = 0;
    rows.forEach((record) => {
      const entry = addAdditionalEntry(list, template, selector, titlePrefix, index++, removeAttribute);
      fields.forEach((field) => {
        entry.querySelector(`[${fieldAttribute}="${field}"]`).value = String(record[field] ?? "");
      });
    });
    if (titlePrefix === "Project") nextProjectIndex = index;
    else nextCertificationIndex = index;
  }

  function normalizeSkills(skills) {
    const normalized = [];
    const seen = new Set();
    (Array.isArray(skills) ? skills : []).forEach((value) => {
      const skill = String(value ?? "").trim();
      const key = skill.toLocaleLowerCase();
      if (skill && !seen.has(key)) {
        seen.add(key);
        normalized.push(skill);
      }
    });
    return normalized;
  }

  function collectExperiencePayload(list, selector, prefix) {
    return [...list.querySelectorAll(selector)].map((entry) => {
      const value = (field) => entry.querySelector(`[data-${prefix}-field="${field}"]`).value.trim();
      return {
        company: value("company"),
        role: value("role"),
        duration: value("duration"),
        responsibilities: value("responsibilities"),
        technologies: value("technologies"),
      };
    }).filter((entry) => Object.values(entry).some(Boolean));
  }

  function buildProfilePayload() {
    const basic = collectBasicInformation();
    basic.educationLevelOther = basic.educationLevel === "other" ? basic.educationLevelOther : "";
    basic.degreeOther = basic.degree === "other" ? basic.degreeOther : "";
    basic.fieldOfStudyOther = basic.fieldOfStudy === "other" ? basic.fieldOfStudyOther : "";

    const additional = collectAdditionalInformation();
    return {
      basicInformation: basic,
      careerSkills: {
        careerStatus: selectedCareerStatus(),
        technicalSkills: normalizeSkills(profileSetupState.careerSkills.technicalSkills),
        internshipExperience: collectExperiencePayload(internshipList, "[data-internship-entry]", "internship"),
        professionalExperience: collectExperiencePayload(experienceList, "[data-experience-entry]", "experience"),
      },
      additionalInformation: {
        projects: additional.projects.map(({ name, description, technologies }) => ({
          name: name.trim(),
          description: description.trim(),
          technologies: technologies.trim(),
        })),
        certifications: additional.certifications.map(({ name, organization }) => ({
          name: name.trim(),
          organization: organization.trim(),
        })),
        achievements: additional.achievements.trim(),
      },
    };
  }

  async function loadSavedProfile() {
    if (profileLoadPromise) return profileLoadPromise;
    profileLoadPromise = (async () => {
      try {
        const profile = await apiGet("/api/profiles/me");
        restoreProfile(profile);
      } catch (error) {
        if (error.status === 404) return;
        if (error.status === 401) {
          window.location.assign("login.html");
          return;
        }
        statusMessage.textContent = "Your saved profile could not be loaded. Your current entries are still available; reload the page to try again.";
        statusMessage.className = "form-status is-error";
        statusMessage.hidden = false;
      }
    })();
    return profileLoadPromise;
  }

  function focusCareerValidationError(field) {
    switchSection(2);
    if (internshipSection.contains(field)) internshipSection.hidden = false;
    if (professionalSection.contains(field)) professionalSection.hidden = false;
    field.focus();
  }

  function showProfileCompletion() {
    profileSetupState.profileStageCompleted = true;
    basicSection.hidden = true;
    careerSection.hidden = true;
    additionalSection.hidden = true;
    profileCompletion.hidden = false;
    setProgress(3, true);
    window.scrollTo(0, 0);
    document.querySelector("#completion-title").focus({ preventScroll: true });
  }

  function showProfileSaveError(error) {
    if (error.status === 401) {
      window.location.assign("login.html");
      return;
    }
    if (error.status === 403) {
      additionalStatusMessage.textContent = "Your secure session could not be verified. Refresh the page and try again.";
    } else if (error.status === 422) {
      additionalStatusMessage.textContent = "Some profile details need attention. Review the required fields and try again.";
    } else {
      additionalStatusMessage.textContent = "Your profile could not be saved. Your information is still here; please try again.";
    }
    additionalStatusMessage.className = "form-status additional-status is-error";
    additionalStatusMessage.hidden = false;
  }

  function restoreProfile(profile) {
    const basic = profile.basicInformation;
    document.querySelector("#first-name").value = basic.firstName ?? "";
    document.querySelector("#last-name").value = basic.lastName ?? "";

    ensureSelectOption(educationSelect, basic.educationLevel);
    dispatchChange(educationSelect);
    ensureSelectOption(degreeSelect, basic.degree);
    dispatchChange(degreeSelect);
    ensureSelectOption(fieldSelect, basic.fieldOfStudy);
    dispatchChange(fieldSelect);

    showOtherInput(otherInputs[0], basic.educationLevel === "other");
    otherInputs[0].input.value = basic.educationLevel === "other" ? (basic.educationLevelOther ?? "") : "";
    showOtherInput(otherInputs[1], basic.degree === "other");
    otherInputs[1].input.value = basic.degree === "other" ? (basic.degreeOther ?? "") : "";
    showOtherInput(otherInputs[2], basic.fieldOfStudy === "other");
    otherInputs[2].input.value = basic.fieldOfStudy === "other" ? (basic.fieldOfStudyOther ?? "") : "";
    profileSetupState.basicInformation = collectBasicInformation();

    const career = profile.careerSkills;
    profileSetupState.careerSkills.technicalSkills = normalizeSkills(career.technicalSkills);
    renderSkills();
    restoreExperienceEntries(internshipList, internshipTemplate, "[data-internship-entry]", "Internship", "internship", "removeInternship", career.internshipExperience);
    restoreExperienceEntries(experienceList, experienceTemplate, "[data-experience-entry]", "Experience", "experience", "removeExperience", career.professionalExperience);
    careerRadios.forEach((radio) => { radio.checked = radio.value === career.careerStatus; });
    const selectedRadio = careerRadios.find((radio) => radio.checked);
    if (selectedRadio) dispatchChange(selectedRadio);
    profileSetupState.careerSkills = collectCareerInformation();

    const additional = profile.additionalInformation;
    restoreAdditionalEntries(projectList, projectTemplate, "[data-project-entry]", "Project", "data-project-field", ["name", "description", "technologies"], "removeProject", additional.projects);
    restoreAdditionalEntries(certificationList, certificationTemplate, "[data-certification-entry]", "Certification", "data-certification-field", ["name", "organization"], "removeCertification", additional.certifications);
    achievements.value = additional.achievements ?? "";
    profileSetupState.additionalInformation = collectAdditionalInformation();
    profileSetupState.profileStageCompleted = false;
    clearStatus();
    clearCareerStatus();
    clearAdditionalStatus();
  }

  function collectAdditionalEntries(list, entrySelector, fieldAttribute, fieldNames) {
    return [...list.querySelectorAll(entrySelector)].map((entry) => {
      const record = {};
      fieldNames.forEach((name) => {
        record[name] = entry.querySelector(`[${fieldAttribute}="${name}"]`).value;
      });
      return record;
    }).filter((record) => Object.values(record).some((value) => value.trim()));
  }

  function collectAdditionalInformation() {
    return {
      projects: collectAdditionalEntries(projectList, "[data-project-entry]", "data-project-field", ["name", "description", "technologies"]),
      certifications: collectAdditionalEntries(certificationList, "[data-certification-entry]", "data-certification-field", ["name", "organization"]),
      achievements: achievements.value,
    };
  }

  function updateAdditionalDraft() {
    profileSetupState.additionalInformation = collectAdditionalInformation();
  }

  function setAdditionalFieldError(field, error, message) {
    error.textContent = message;
    field.setAttribute("aria-invalid", String(Boolean(message)));
    return !message;
  }

  function validateAdditionalInformation() {
    clearAdditionalStatus();
    let firstInvalid = null;
    const markInvalid = (field) => { if (!firstInvalid) firstInvalid = field; };

    projectList.querySelectorAll("[data-project-entry]").forEach((entry) => {
      const fields = ["name", "description", "technologies"].map((name) => entry.querySelector(`[data-project-field="${name}"]`));
      const started = fields.some((field) => field.value.trim());
      fields.forEach((field) => {
        const error = entry.querySelector(`[data-project-error="${field.dataset.projectField}"]`);
        const message = started && !field.value.trim() ? `${field.labels[0].textContent.trim()} is required for a started project.` : "";
        setAdditionalFieldError(field, error, message);
        if (message) markInvalid(field);
      });
    });

    certificationList.querySelectorAll("[data-certification-entry]").forEach((entry) => {
      const fields = ["name", "organization"].map((name) => entry.querySelector(`[data-certification-field="${name}"]`));
      const started = fields.some((field) => field.value.trim());
      fields.forEach((field) => {
        const error = entry.querySelector(`[data-certification-error="${field.dataset.certificationField}"]`);
        const message = started && !field.value.trim() ? `${field.labels[0].textContent.trim()} is required for a started certification.` : "";
        setAdditionalFieldError(field, error, message);
        if (message) markInvalid(field);
      });
    });

    updateAdditionalDraft();
    return firstInvalid;
  }

  function clearAdditionalStatus() {
    additionalStatusMessage.hidden = true;
    additionalStatusMessage.textContent = "";
    additionalStatusMessage.classList.remove("is-success", "is-error");
  }

  function validateCareerInformation() {
    clearCareerStatus();
    let firstInvalid = null;
    const markInvalid = (element) => { if (!firstInvalid) firstInvalid = element; };

    const statusGroup = document.querySelector(".career-options");
    const statusError = document.querySelector("#career-status-error");
    const hasStatus = Boolean(selectedCareerStatus());
    setFieldError(statusGroup, statusError, hasStatus ? "" : "Select your current career status.");
    if (!hasStatus) markInvalid(careerRadios[0]);

    profileSetupState.careerSkills.technicalSkills = normalizeSkills(profileSetupState.careerSkills.technicalSkills);
    const hasSkills = profileSetupState.careerSkills.technicalSkills.length > 0;
    skillError.textContent = hasSkills ? "" : "Add at least one technical skill to continue.";
    skillInput.setAttribute("aria-invalid", String(!hasSkills));
    if (!hasSkills) markInvalid(skillInput);
    const overlongSkill = profileSetupState.careerSkills.technicalSkills.find((skill) => skill.length > 100);
    if (overlongSkill) {
      skillError.textContent = "Skills must be 100 characters or fewer.";
      skillInput.setAttribute("aria-invalid", "true");
      markInvalid(skillInput);
    }

    [
      { list: internshipList, selector: "[data-internship-entry]", prefix: "internship", message: "Enter your internship role." },
      { list: experienceList, selector: "[data-experience-entry]", prefix: "experience", message: "Enter a job title or role." },
    ].forEach(({ list, selector, prefix, message }) => {
      list.querySelectorAll(selector).forEach((entry) => {
        const fields = ["company", "role", "duration", "responsibilities", "technologies"].map((key) => entry.querySelector(`[data-${prefix}-field="${key}"]`));
        const started = fields.some((field) => field.value.trim());
        const role = entry.querySelector(`[data-${prefix}-field="role"]`);
        const error = entry.querySelector(`[data-${prefix}-error="role"]`);
        const invalid = started && !role.value.trim();
        setFieldError(role, error, invalid ? message : "");
        if (invalid) markInvalid(role);
      });
    });

    updateCareerDraft();
    return firstInvalid;
  }

  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (isSavingProfile) return;
    if (!additionalSection.hidden) {
      const invalidBasicField = validateBasicInformation();
      if (invalidBasicField) {
        switchSection(1);
        invalidBasicField.focus();
        return;
      }
      const invalidCareerField = validateCareerInformation();
      if (invalidCareerField) {
        careerStatusMessage.textContent = "Please complete the required career and experience fields.";
        careerStatusMessage.className = "form-status career-status is-error";
        careerStatusMessage.hidden = false;
        focusCareerValidationError(invalidCareerField);
        return;
      }
      const firstInvalid = validateAdditionalInformation();
      if (firstInvalid) {
        additionalStatusMessage.textContent = "Complete the missing fields in each project or certification you started.";
        additionalStatusMessage.className = "form-status additional-status is-error";
        additionalStatusMessage.hidden = false;
        firstInvalid.focus();
        return;
      }
      profileSetupState.additionalInformation = collectAdditionalInformation();
      const submitButton = additionalSection.querySelector('button[type="submit"]');
      isSavingProfile = true;
      submitButton.disabled = true;
      additionalStatusMessage.textContent = "Saving your profile…";
      additionalStatusMessage.className = "form-status additional-status";
      additionalStatusMessage.hidden = false;
      try {
        await apiPut("/api/profiles/me", buildProfilePayload());
        showProfileCompletion();
      } catch (error) {
        showProfileSaveError(error);
      } finally {
        isSavingProfile = false;
        submitButton.disabled = false;
      }
      return;
    }

    if (!careerSection.hidden) {
      const firstInvalid = validateCareerInformation();
      if (firstInvalid) {
        careerStatusMessage.textContent = "Please complete the required career and experience fields.";
        careerStatusMessage.className = "form-status career-status is-error";
        careerStatusMessage.hidden = false;
        focusCareerValidationError(firstInvalid);
        return;
      }
      profileSetupState.careerSkills = collectCareerInformation();
      switchSection(3);
      return;
    }

    const invalidBasicField = validateBasicInformation();
    if (invalidBasicField) {
      invalidBasicField.focus();
      return;
    }

    profileSetupState.basicInformation = collectBasicInformation();
    switchSection(2);
    skillInput.focus({ preventScroll: true });
  });

  document.querySelector("#previous-section-button").addEventListener("click", () => {
    updateCareerDraft();
    clearCareerStatus();
    switchSection(1);
  });

  document.querySelector("#previous-additional-button").addEventListener("click", () => {
    updateAdditionalDraft();
    clearAdditionalStatus();
    switchSection(2);
  });

  document.querySelector("#add-project-button").addEventListener("click", () => {
    const entry = addAdditionalEntry(projectList, projectTemplate, "[data-project-entry]", "Project", nextProjectIndex++, "removeProject");
    clearAdditionalStatus();
    updateAdditionalDraft();
    entry.querySelector('[data-project-field="name"]').focus();
  });

  projectList.addEventListener("input", (event) => {
    const field = event.target.closest("[data-project-field]");
    if (!field) return;
    const entry = field.closest("[data-project-entry]");
    const error = entry.querySelector(`[data-project-error="${field.dataset.projectField}"]`);
    error.textContent = "";
    field.removeAttribute("aria-invalid");
    clearAdditionalStatus();
    updateAdditionalDraft();
  });
  projectList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-project]");
    if (!button) return;
    button.closest("[data-project-entry]").remove();
    renumberAdditionalEntries(projectList, "[data-project-entry]", "Project", "removeProject");
    clearAdditionalStatus();
    updateAdditionalDraft();
  });

  document.querySelector("#add-certification-button").addEventListener("click", () => {
    const entry = addAdditionalEntry(certificationList, certificationTemplate, "[data-certification-entry]", "Certification", nextCertificationIndex++, "removeCertification");
    clearAdditionalStatus();
    updateAdditionalDraft();
    entry.querySelector('[data-certification-field="name"]').focus();
  });

  certificationList.addEventListener("input", (event) => {
    const field = event.target.closest("[data-certification-field]");
    if (!field) return;
    const entry = field.closest("[data-certification-entry]");
    const error = entry.querySelector(`[data-certification-error="${field.dataset.certificationField}"]`);
    error.textContent = "";
    field.removeAttribute("aria-invalid");
    clearAdditionalStatus();
    updateAdditionalDraft();
  });
  certificationList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-certification]");
    if (!button) return;
    button.closest("[data-certification-entry]").remove();
    renumberAdditionalEntries(certificationList, "[data-certification-entry]", "Certification", "removeCertification");
    clearAdditionalStatus();
    updateAdditionalDraft();
  });

  achievements.addEventListener("input", () => {
    clearAdditionalStatus();
    updateAdditionalDraft();
  });

  careerRadios.forEach((radio) => radio.addEventListener("change", () => {
    document.querySelector(".career-options").removeAttribute("aria-invalid");
    document.querySelector("#career-status-error").textContent = "";
    const isExperienced = selectedCareerStatus() === "Experienced";
    internshipSection.hidden = !selectedCareerStatus() || isExperienced;
    professionalSection.hidden = !isExperienced;
    clearCareerStatus();
    updateCareerDraft();
  }));

  document.querySelector("#add-skill-button").addEventListener("click", addSkill);
  skillInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addSkill();
    }
  });
  skillInput.addEventListener("input", () => {
    skillError.textContent = "";
    skillInput.removeAttribute("aria-invalid");
    clearCareerStatus();
  });
  skillList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-skill]");
    if (!button) return;
    const skill = button.dataset.removeSkill;
    profileSetupState.careerSkills.technicalSkills = profileSetupState.careerSkills.technicalSkills.filter((entry) => entry !== skill);
    renderSkills();
    clearCareerStatus();
    updateCareerDraft();
  });

  function attachExperienceHandlers(list, entrySelector, fieldPrefix, titlePrefix, removeSelector, removeDataKey, template, counter) {
    list.addEventListener("input", (event) => {
      const field = event.target.closest(`[data-${fieldPrefix}-field]`);
      if (!field) return;
      const entry = field.closest(entrySelector);
      const error = entry.querySelector(`[data-${fieldPrefix}-error="${field.dataset[`${fieldPrefix}Field`]}"]`);
      if (error) error.textContent = "";
      field.removeAttribute("aria-invalid");
      clearCareerStatus();
      updateCareerDraft();
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest(removeSelector);
      if (!button) return;
      button.closest(entrySelector).remove();
      renumberCareerEntries(list, entrySelector, titlePrefix, removeSelector);
      clearCareerStatus();
      updateCareerDraft();
    });
    const addButtonId = fieldPrefix === "internship" ? "#add-internship-button" : "#add-experience-button";
    document.querySelector(addButtonId).addEventListener("click", () => {
      const index = fieldPrefix === "internship" ? nextInternshipIndex++ : nextExperienceIndex++;
      addCareerEntry(list, template, entrySelector, titlePrefix, index, removeDataKey, fieldPrefix);
    });
  }

  attachExperienceHandlers(internshipList, "[data-internship-entry]", "internship", "Internship", "[data-remove-internship]", "removeInternship", internshipTemplate, nextInternshipIndex);
  attachExperienceHandlers(experienceList, "[data-experience-entry]", "experience", "Experience", "[data-remove-experience]", "removeExperience", experienceTemplate, nextExperienceIndex);

  loadSavedProfile();
}
