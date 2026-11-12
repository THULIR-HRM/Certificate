document.addEventListener("DOMContentLoaded", () => {
  const normalizeCatalogSearch = (value) => value.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
  const catalogFromPage = (id) => JSON.parse(document.getElementById(id)?.textContent || "[]");

  document.querySelectorAll("[data-degree-catalog]").forEach((container) => {
    const degrees = catalogFromPage("degree-catalog-data");
    const specializations = catalogFromPage("specialization-catalog-data");
    const search = container.querySelector('[name="degree_search"]');
    const degreeSelect = container.querySelector('[name="degree_option"]');
    const specializationSelect = container.querySelector('[name="specialization_option"]');
    const degreeOther = container.querySelector("[data-degree-other]");
    const specializationOther = container.querySelector("[data-specialization-other]");
    const degreeById = new Map(degrees.map((degree) => [degree.id, degree]));
    const specializationById = new Map(specializations.map((item) => [item.id, item]));
    if (!search || !degreeSelect || !specializationSelect) return;

    degreeSelect.querySelectorAll("option").forEach((option) => {
      const degree = degreeById.get(option.value);
      if (degree) {
        option.dataset.searchText = normalizeCatalogSearch(
          [degree.abbreviation, degree.full_name, ...degree.aliases].join(" ")
        );
      }
    });
    specializationSelect.querySelectorAll("option").forEach((option) => {
      const item = specializationById.get(option.value);
      if (item) {
        option.dataset.degreeId = item.degree_id;
        option.dataset.isOther = String(item.name.toLocaleLowerCase() === "other");
      }
    });

    const updateSpecializations = () => {
      const degreeId = degreeSelect.value;
      if (degreeOther) degreeOther.hidden = degreeId !== "__other__";
      specializationSelect.querySelectorAll("option").forEach((option) => {
        if (!option.value || option.value === "__other__") {
          option.hidden = false;
          return;
        }
        option.hidden = !degreeId || degreeId === "__other__" || option.dataset.degreeId !== degreeId;
      });
      if (degreeId === "__other__" && specializationSelect.value !== "__other__") {
        specializationSelect.value = "";
      }
      updateSpecializationOther();
    };
    const updateSpecializationOther = () => {
      if (specializationOther) {
        const selected = specializationSelect.selectedOptions[0];
        specializationOther.hidden = !(
          specializationSelect.value === "__other__" || selected?.dataset.isOther === "true"
        );
      }
    };

    search.addEventListener("input", () => {
      const query = normalizeCatalogSearch(search.value.trim());
      let exactAlias = null;
      degreeSelect.querySelectorAll("option").forEach((option) => {
        const searchText = option.dataset.searchText || "";
        option.hidden = Boolean(query && !searchText.includes(query));
        const degree = degreeById.get(option.value);
        if (
          degree &&
          [degree.abbreviation, degree.full_name, ...degree.aliases].some(
            (alias) => normalizeCatalogSearch(alias) === query
          )
        ) {
          exactAlias = degree.id;
        }
      });
      if (exactAlias) {
        degreeSelect.value = exactAlias;
        updateSpecializations();
      }
    });
    degreeSelect.addEventListener("change", updateSpecializations);
    specializationSelect.addEventListener("change", updateSpecializationOther);
    updateSpecializations();
  });

  document.querySelectorAll("[data-college-catalog]").forEach((container) => {
    const colleges = catalogFromPage("college-catalog-data");
    const search = container.querySelector('[name="college_search"]');
    const stateFilter = container.querySelector('[name="college_state_filter"]');
    const districtFilter = container.querySelector('[name="college_district_filter"]');
    const categoryFilter = container.querySelector('[name="college_category_filter"]');
    const affiliationFilter = container.querySelector('[name="college_affiliation_filter"]');
    const collegeSelect = container.querySelector('[name="college_option"]');
    const collegeValue = container.closest("form")?.querySelector('[name="college"]');
    const results = container.querySelector('[role="listbox"]');
    const collegeOther = container.querySelector("[data-college-other]");
    const collegeById = new Map(colleges.map((college) => [college.id, college]));
    if (!search || !collegeSelect || !results) return;

    collegeSelect.querySelectorAll("option").forEach((option) => {
      const college = collegeById.get(option.value);
      if (college) {
        option.dataset.searchText = normalizeCatalogSearch(
          [
            college.name,
            ...(college.aliases || []),
            college.location,
            college.address,
            college.state,
            college.district,
            college.category,
            college.affiliation,
          ].join(" ")
        );
      }
    });
    let isOpen = false;
    let activeIndex = -1;
    let selectedSearchValue = "";
    const selectedOption = collegeSelect.selectedOptions[0];
    if (selectedOption?.value) {
      search.value = selectedOption.textContent.trim();
      selectedSearchValue = search.value;
    }
    collegeSelect.hidden = true;

    const passesFilters = (college) => {
      const state = stateFilter?.value || "";
      const district = districtFilter?.value || "";
      const category = categoryFilter?.value || "";
      const affiliation = normalizeCatalogSearch(affiliationFilter?.value.trim() || "");
      return (
        (!state || college.state === state) &&
        (!district || college.district_id === district) &&
        (!category || college.category_id === category) &&
        (!affiliation || normalizeCatalogSearch(college.affiliation).includes(affiliation))
      );
    };
    const updateOtherFields = () => {
      if (collegeOther) {
        collegeOther.hidden = !["__other__", "__other_state__"].includes(collegeSelect.value);
      }
    };
    const closeResults = () => {
      isOpen = false;
      results.hidden = true;
      search.setAttribute("aria-expanded", "false");
      search.removeAttribute("aria-activedescendant");
      activeIndex = -1;
    };
    const renderResults = (open = isOpen) => {
      const query = normalizeCatalogSearch(search.value.trim());
      const options = [...collegeSelect.options].filter((option) => {
        const college = collegeById.get(option.value);
        if (!option.value) return false;
        if (!college) return true;
        return (
          passesFilters(college) &&
          (!query || (option.dataset.searchText || "").includes(query))
        );
      });
      results.replaceChildren();
      if (!options.length) {
        const empty = document.createElement("div");
        empty.className = "college-empty-results";
        empty.textContent = "No colleges match. Try changing your search or filters.";
        results.append(empty);
      } else {
        options.forEach((option, index) => {
          const item = document.createElement("div");
          item.className = "college-result";
          item.id = `college-result-${index}`;
          item.setAttribute("role", "option");
          item.setAttribute("aria-selected", String(option.value === collegeSelect.value));
          item.dataset.collegeValue = option.value;
          if (index === activeIndex) {
            item.classList.add("is-active");
            search.setAttribute("aria-activedescendant", item.id);
          }
          item.textContent = option.textContent.trim();
          results.append(item);
        });
      }
      results.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
      isOpen = open;
      results.hidden = !open;
      search.setAttribute("aria-expanded", String(open));
    };
    const selectOption = (option) => {
      if (!option) return;
      collegeSelect.value = option.dataset.collegeValue;
      const college = collegeById.get(collegeSelect.value);
      search.value = college ? `${college.name}, ${college.location}` : option.textContent.trim();
      selectedSearchValue = search.value;
      if (collegeValue) {
        collegeValue.value = college ? `${college.name}, ${college.location}` : "";
      }
      updateOtherFields();
      closeResults();
      renderResults(false);
    };
    const updateFilters = () => {
      const selectedCollege = collegeById.get(collegeSelect.value);
      if (selectedCollege && !passesFilters(selectedCollege)) {
        collegeSelect.value = "";
        selectedSearchValue = "";
        search.value = "";
        if (collegeValue) collegeValue.value = "";
      }
      activeIndex = -1;
      renderResults();
    };
    search.addEventListener("focus", () => renderResults(true));
    search.addEventListener("input", () => {
      if (search.value !== selectedSearchValue) {
        collegeSelect.value = "";
        selectedSearchValue = "";
        if (collegeValue) collegeValue.value = "";
        updateOtherFields();
      }
      activeIndex = -1;
      renderResults(true);
    });
    search.addEventListener("keydown", (event) => {
      const options = [...results.querySelectorAll('[role="option"]')];
      if (event.key === "Escape") {
        closeResults();
      } else if (event.key === "ArrowDown" && options.length) {
        event.preventDefault();
        activeIndex = (activeIndex + 1) % options.length;
        renderResults(true);
      } else if (event.key === "ArrowUp" && options.length) {
        event.preventDefault();
        activeIndex = (activeIndex <= 0 ? options.length : activeIndex) - 1;
        renderResults(true);
      } else if (event.key === "Enter" && isOpen) {
        const option = options[activeIndex];
        if (option) {
          event.preventDefault();
          selectOption(option);
        }
      }
    });
    results.addEventListener("mousedown", (event) => event.preventDefault());
    results.addEventListener("click", (event) => {
      const option = event.target.closest("[data-college-value]");
      if (option) selectOption(option);
    });
    [stateFilter, districtFilter, categoryFilter, affiliationFilter].forEach((field) => {
      field?.addEventListener("input", updateFilters);
      field?.addEventListener("change", updateFilters);
    });
    collegeSelect.addEventListener("change", updateOtherFields);
    document.addEventListener("click", (event) => {
      if (!container.contains(event.target)) closeResults();
    });
    updateOtherFields();
    renderResults(false);
  });

  document.querySelectorAll("[data-domain-add]").forEach((container) => {
    const toggle = container.querySelector("[data-domain-toggle]");
    const input = container.querySelector("[data-domain-input]");
    if (!toggle || !input) return;
    toggle.addEventListener("click", () => {
      const open = input.hidden;
      input.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (open) input.querySelector("input")?.focus();
    });
  });

  document.querySelectorAll("textarea[maxlength]").forEach((textarea) => {
    const counter = textarea.closest(".field")?.querySelector("[data-character-count]");
    if (!counter) return;
    const update = () => { counter.textContent = String(textarea.value.length); };
    textarea.addEventListener("input", update);
    update();
  });

  document.querySelectorAll("[data-apply-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      const field = button.closest(".field");
      const input = field?.querySelector('input[name="degree_department"]');
      const suggestion = field?.querySelector("[data-suggested-format]")?.textContent?.trim();
      if (!input || !suggestion) return;
      input.value = suggestion;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      button.textContent = "Suggestion applied";
    });
  });

  document.querySelectorAll("[data-submit-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!form.checkValidity()) return;
      const button = form.querySelector("[data-submit-button]");
      if (!button) return;
      if (button.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      button.dataset.submitting = "true";
      button.disabled = true;
      const label = button.querySelector("[data-submit-label]");
      if (label) label.textContent = "Submitting...";
    });
  });

  document.querySelectorAll("[data-confirm]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!window.confirm(button.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-copy-button]").forEach((button) => {
    button.addEventListener("click", async () => {
      const input = button.closest(".qr-card")?.querySelector("[data-copy-value]");
      if (!input) return;
      try {
        await navigator.clipboard.writeText(input.value);
        button.textContent = "Copied";
      } catch {
        input.select();
        document.execCommand("copy");
        button.textContent = "Copied";
      }
    });
  });

  document.querySelectorAll("[data-refresh-qr]").forEach((button) => {
    button.addEventListener("click", () => {
      const image = button.closest(".qr-card")?.querySelector("[data-qr-image]");
      if (!image) return;
      const url = new URL(image.src, window.location.href);
      url.searchParams.set("refresh", String(Date.now()));
      image.src = url.toString();
    });
  });

  const genderInputs = document.querySelectorAll('input[name="gender"]');
  const titleSelect = document.querySelector('select[name="title"]');
  if (titleSelect && genderInputs.length) {
    genderInputs.forEach((input) => {
      input.addEventListener("change", () => {
        titleSelect.value = input.value === "male" ? "Mr." : "Ms.";
      });
    });
  }
});
