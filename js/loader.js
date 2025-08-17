// Dynamically load header and footer into each page
document.addEventListener("DOMContentLoaded", function() {
    loadHTML("header.html", "site-header");
    loadHTML("footer.html", "site-footer");
});

function loadHTML(file, elementId) {
    fetch(file)
        .then(response => {
            if (!response.ok) throw new Error(`Error loading ${file}`);
            return response.text();
        })
        .then(data => {
            document.getElementById(elementId).innerHTML = data;
        })
        .catch(err => console.error(err));
}