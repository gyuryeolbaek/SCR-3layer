// effective conductivity: porous one-equation model (Eq. 5) / fluid + turbulent
{
    scalarField& kI = kappaEff.primitiveFieldRef();
    const scalarField& nutI = nut.primitiveField();
    const scalar rhoCp = rho.value()*cp.value();
    forAll(kI, c)
    {
        if (mask[c] > 0.5)
        {
            const scalar lS = lambdaSa + lambdaSb*T[c];
            kI[c] = -2*lS + 1/(porosity/(2*lS + lambdaF) + (1 - porosity)/(3*lS));
        }
        else
        {
            kI[c] = lambdaF + rhoCp*nutI[c]/Prt;
        }
    }
    kappaEff.correctBoundaryConditions();   // coupled patches
    forAll(kappaEff.boundaryField(), patchi)
    {
        if (mesh.boundary()[patchi].coupled()) continue;
        fvPatchScalarField& kp = kappaEff.boundaryFieldRef()[patchi];
        const labelUList& fc = mesh.boundary()[patchi].faceCells();
        const scalarField& nutp = nut.boundaryField()[patchi];
        forAll(kp, f)
        {
            const label c = fc[f];
            kp[f] = mask[c] > 0.5 ? kI[c] : lambdaF + rhoCp*nutp[f]/Prt;
        }
    }

    // convective wall: q = h (Tamb - Tw) with wall conduction kappa dT/dn (series resistance)
    for (const label patchi : wallIDs)
    {
        mixedFvPatchScalarField& Tp = refCast<mixedFvPatchScalarField>(T.boundaryFieldRef()[patchi]);
        const scalarField& kw = kappaEff.boundaryField()[patchi];
        const scalarField& dc = mesh.boundary()[patchi].deltaCoeffs();
        Tp.refValue() = Tamb;
        Tp.refGrad() = 0;
        Tp.valueFraction() = hWall/(hWall + kw*dc);
    }
}
