// Wait for the page to load before hiding the loader
window.addEventListener('load', () => {
    hideLoader();  // Hide loader after page load
});

function showLoader() {
    document.getElementById('loader').classList.remove('hidden');
}

function hideLoader() {
    document.getElementById('loader').classList.add('hidden');
}

uploadBtn.addEventListener('click', () => {
    pdfUpload.click();
});

pdfUpload.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        fileName.textContent = file.name;
        cleanupAndUploadPDF(file);
    }
});

function cleanupAndUploadPDF(file) {
    // Show loader while uploading
    showLoader();
    
    fetch('/cleanup', { method: 'POST' })
        .then(() => uploadPDF(file))
        .catch(error => {
            console.error('Error during cleanup:', error);
            hideLoader();  // Hide loader on error
        });
}

function uploadPDF(file) {
    const formData = new FormData();
    formData.append('file', file);

    fetch('/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.summary) {
            addMessage('AI', data.summary);
        }
        hideLoader();  // Hide loader after upload completes
    })
    .catch(error => {
        console.error('Error:', error);
        addMessage('AI', 'Sorry, something went wrong uploading the PDF.');
        hideLoader();  // Hide loader on error
    });
}

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

function sendMessage() {
    const message = messageInput.value.trim();
    if (!message) return;

    addMessage('You', message);
    messageInput.value = '';

    // Show loader when sending message
    showLoader();
    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: message })
    })
    .then(response => response.json())
    .then(data => {
        if (data.response) {
            addMessage('AI', data.response);
        }
        hideLoader();  // Hide loader after response
    })
    .catch(error => {
        console.error('Error:', error);
        addMessage('AI', 'Sorry, something went wrong.');
        hideLoader();  // Hide loader on error
    });
}

function addMessage(sender, text) {
    const messageDiv = document.createElement('div');
    messageDiv.classList.add('message', 'p-3', 'rounded-lg', 'max-w-xl', 'break-words');
    
    if (sender === 'You') {
        messageDiv.classList.add('bg-blue-100', 'self-end', 'ml-auto');
        messageDiv.innerHTML = `<strong>${sender}:</strong> ${marked.parse(text)}`;
    } else {
        messageDiv.classList.add('bg-gray-200', 'self-start');
        messageDiv.innerHTML = `<strong>${sender}:</strong> ${marked.parse(text)}`;
    }

    chatContainer.appendChild(messageDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}


// Cleanup on unload
window.addEventListener('beforeunload', () => {
    fetch('/cleanup', { method: 'POST' });
});
