// ---------------------------------------------------------------------------
Info<< "Reading constant/scrProperties\n" << endl;
IOdictionary scrProps
(
    IOobject("scrProperties", runTime.constant(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE)
);

const dictionary& gasD = scrProps.subDict("gas");
const dimensionedScalar rho("rho", dimDensity, gasD.get<scalar>("rho"));
const dimensionedScalar cp("cp", dimSpecificHeatCapacity, gasD.get<scalar>("cp"));
const scalar lambdaF = gasD.get<scalar>("lambdaF");
const dimensionedScalar Dm("Dm", dimViscosity, gasD.get<scalar>("Dm"));
const scalar Prt = gasD.get<scalar>("Prt");
const scalar Sct = gasD.get<scalar>("Sct");
const scalar Mmix = gasD.get<scalar>("Mmix");     // g/mol
const scalar MNO  = gasD.get<scalar>("MNO");
const scalar MNH3 = gasD.get<scalar>("MNH3");
const scalar MO2  = gasD.get<scalar>("MO2");
const scalar MH2O = gasD.get<scalar>("MH2O");
const scalar XNOin = gasD.get<scalar>("XNOin");

const dictionary& catD = scrProps.subDict("catalyst");
const wordList zoneNames(catD.get<wordList>("zones"));
const scalar porosity = catD.get<scalar>("porosity");
const scalar sV = catD.get<scalar>("sV");
const scalar lambdaSa = catD.get<scalar>("lambdaSa");
const scalar lambdaSb = catD.get<scalar>("lambdaSb");

const dictionary& kinD = catD.subDict("kinetics");
const word kinModel(kinD.get<word>("model"));          // chatterjeeCoverage | acePseudoGas
if (kinModel != "chatterjeeCoverage" && kinModel != "acePseudoGas")
{
    FatalErrorInFunction << "kinetics model must be chatterjeeCoverage or acePseudoGas, got "
        << kinModel << exit(FatalError);
}
const bool chatterjee = (kinModel == "chatterjeeCoverage");
const scalar fCat = catD.getOrDefault<scalar>("solidFraction", 1 - catD.get<scalar>("porosity"));
const scalar pRef = scrProps.getOrDefault<scalar>("pRef", 101325);
Info<< "Reaction model: " << kinModel
    << (chatterjee ? "  (rates per catalyst volume x solid fraction " : "  (rates per surface area x sV ")
    << (chatterjee ? fCat : sV) << ")" << nl << endl;
const scalar A1a = kinD.get<scalar>("A1ads");
const scalar A1d = kinD.get<scalar>("A1des");
const scalar E1d = kinD.get<scalar>("E1des");      // J/mol
const scalar gammaDes = kinD.get<scalar>("gamma");
const scalar A2 = kinD.get<scalar>("A2");
const scalar E2 = kinD.get<scalar>("E2");
const scalar A3 = kinD.get<scalar>("A3");
const scalar E3 = kinD.get<scalar>("E3");
const scalar beta = kinD.get<scalar>("beta");
const scalar KLH = kinD.get<scalar>("KLH");
const scalar dH2 = kinD.get<scalar>("dH2");        // J/mol NH3*, exothermic > 0
const scalar dH3 = kinD.get<scalar>("dH3");

const dictionary& wallD = scrProps.subDict("wall");
const wordList wallNames(wallD.get<wordList>("patches"));
const scalar hWall = wallD.get<scalar>("h");
const scalar Tamb = wallD.get<scalar>("Tamb");
const word outletName(scrProps.get<word>("outlet"));

const dictionary& convD = scrProps.subDict("convergence");
const scalar resTol = convD.get<scalar>("residual");
const scalar etaTol = convD.get<scalar>("dEta");
const label nStable = convD.get<label>("nStable");

labelList wallIDs(wallNames.size());
forAll(wallNames, i)
{
    wallIDs[i] = mesh.boundaryMesh().findPatchID(wallNames[i]);
    if (wallIDs[i] < 0)
    {
        FatalErrorInFunction << "wall patch " << wallNames[i] << " not found" << exit(FatalError);
    }
}

// ---------------------------------------------------------------------------
Info<< "Reading frozen flow (phi, nut)\n" << endl;
surfaceScalarField phi
(
    IOobject("phi", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
);
volScalarField nut
(
    IOobject("nut", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE), mesh
);
const surfaceScalarField rhoPhi("rhoPhi", rho*phi);

Info<< "Reading T, NO, NH3, O2, H2O\n" << endl;
volScalarField T(IOobject("T", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
volScalarField NO(IOobject("NO", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
volScalarField NH3(IOobject("NH3", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
volScalarField O2(IOobject("O2", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
volScalarField H2O(IOobject("H2O", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
// NH3*: surface-adsorbed NH3 represented as a transported pseudo-species (as in the CFD-ACE+ model)
volScalarField NH3s(IOobject("NH3s", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
// total ammonia NH3 + NH3*: same boundary condition types as NH3
volScalarField NH3tot(IOobject("NH3tot", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE), NH3);
NH3tot = NH3 + NH3s;

for (const label patchi : wallIDs)
{
    if (!isA<mixedFvPatchScalarField>(T.boundaryField()[patchi]))
    {
        FatalErrorInFunction << "T on wall patch " << mesh.boundary()[patchi].name()
            << " must be of type mixed (convective wall set by solver)" << exit(FatalError);
    }
}

auto makeOut = [&](const word& name, const dimensionSet& dims)
{
    return volScalarField
    (
        IOobject(name, runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE),
        mesh, dimensionedScalar(dims, Zero)
    );
};
volScalarField R4(makeOut("R_SCR", dimless));         // SCR rate (chatterjee: mol/m3_cat/s, ace: mol/m2/s)
volScalarField theta(makeOut("theta", dimless));      // chatterjee: NH3 coverage, ace: X_NH3*
volScalarField XNO_ppm(makeOut("XNO_ppm", dimless));
volScalarField XNH3_ppm(makeOut("XNH3_ppm", dimless));
volScalarField XNH3s_ppm(makeOut("XNH3s_ppm", dimless));
volScalarField kappaEff(makeOut("kappaEff", dimPower/dimLength/dimTemperature));

volScalarField mask
(
    IOobject("porousMask", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE),
    mesh, dimensionedScalar(dimless, Zero)
);
for (const word& zn : zoneNames)
{
    const label zi = mesh.cellZones().findZoneID(zn);
    if (zi < 0)
    {
        FatalErrorInFunction << "cellZone " << zn << " not found" << exit(FatalError);
    }
    for (const label c : mesh.cellZones()[zi]) mask[c] = 1;
}
mask.correctBoundaryConditions();
forAll(mask.boundaryField(), patchi)
{
    if (!mesh.boundary()[patchi].coupled())
    {
        mask.boundaryFieldRef()[patchi] = mask.boundaryField()[patchi].patchInternalField();
    }
}
Info<< "Reactive porous cells: " << returnReduce(label(sum(mask.primitiveField())), sumOp<label>())
    << " in zones " << zoneNames << nl << endl;
