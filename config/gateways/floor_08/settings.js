module.exports = {
    flowFile: 'flows.json',
    flowFilePretty: true,
    credentialSecret: process.env.NODE_RED_CREDENTIAL_SECRET || 'gw-secret',
    functionGlobalContext: {},
    uiPort: process.env.PORT || 1880,
    httpAdminRoot: '/admin',
    httpNodeRoot: '/',
};
